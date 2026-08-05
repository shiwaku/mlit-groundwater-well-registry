#!/usr/bin/env python3
"""公開用地下水調査地点位置図（平成24年度・PDF37本）から井戸の実座標を復元する。

原本の台帳には経緯度が無いが、同じサイトで参考公開されている位置図PDFには
平成24年度に調査された井戸が点で描かれている。これは ArcMap が原データの
GISレイヤから出力したベクタPDFなので、点の位置は原データの座標そのものである。
ラベルに `整理番号 ,市町村コード ,地下水使用目的` が併記されているため、
台帳の行と1対1で対応づけられる。

得られる563点は全71,198行の0.8%にすぎないが、ジオコーディング結果の精度を
推定ではなく実測で評価できる唯一の正解データになる（scripts/10_eval_accuracy.py）。

GeoPDF ではないので座標系情報は持たない。図中の行政界を N03（行政区域）に
重ね合わせて変換式を推定する。詳細は scripts/lib/pdf_geo.py を参照。

使い方:
  python3 scripts/09_pdf_truth.py                 # 37都道府県すべて
  python3 scripts/09_pdf_truth.py --prefs 01,13   # 指定県のみ
  python3 scripts/09_pdf_truth.py --gpkg          # QGIS確認用の点GeoPackageも出す
"""
import argparse
import re
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import fitz
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import pdf_geo as pg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data" / "raw" / "f9_pdf"
N03_DIR = ROOT / "data" / "n03"
OUT = ROOT / "output" / "f9_pdf_truth_2012.csv"
OUT_GPKG = ROOT / "output" / "f9_pdf_truth_2012.gpkg"

INDEX_URL = "https://nlftp.mlit.go.jp/kokjo/inspect/landclassification/water/f9_exp.html"
PDF_BASE = "https://nlftp.mlit.go.jp/kokjo/tochimizu/F9/pdf"
N03_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2024/N03-20240101_{p}_GML.zip"
UA = {"User-Agent": "mlit-groundwater-well-registry/1.0 (+research use)"}

# 採用条件: 点の9割以上がラベルの市町村（の1km以内）に収まること。
# 残差では判定しない。総描の差がそのまま乗るため県ごとに水準が違い、
# また図を縮めた偽解の方が残差が小さくなることがある。市町村コードは
# ラベルから独立に読めるので、これが唯一の外部基準になる。
IN_MUNI_MIN = 0.9
NEAR_KM = 1.0


def fetch(url: str, dest: Path, retries: int = 3) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            print(f"    取得 {dest.name}: {len(body):,} bytes")
            return True
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                print(f"    失敗 {url} ({e})")
                return False
            time.sleep(2 * (i + 1))
    return False


def pdf_prefs() -> list[str]:
    """位置図PDFが公開されている都道府県コードを一覧ページから拾う。"""
    req = urllib.request.Request(INDEX_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        html = r.read().decode("utf-8", "replace")
    return sorted(set(re.findall(r"/kokjo/tochimizu/F9/pdf/(\d{2})\.pdf", html)))


def n03(pref: str) -> gpd.GeoDataFrame:
    """N03（行政区域）を都道府県単位で読む。"""
    zp = N03_DIR / f"{pref}.zip"
    if not fetch(N03_URL.format(p=pref), zp):
        raise RuntimeError(f"N03 {pref} を取得できない")
    d = N03_DIR / pref
    if not d.exists():
        with zipfile.ZipFile(zp) as z:
            z.extractall(d)
    shp = next(p for p in d.glob("*.shp") if "subprefecture" not in p.name)
    g = gpd.read_file(shp)
    g = g[g.geometry.notna()].copy()
    g["code"] = g["N03_007"]
    return g


def ring_points(geoms) -> np.ndarray:
    out = []
    for geom in geoms:
        for poly in getattr(geom, "geoms", [geom]):
            out.append(np.asarray(poly.exterior.coords))
            out += [np.asarray(r.coords) for r in poly.interiors]
    return np.vstack(out)


def _refine(cloud, ref, M0, d0, ppk, lat0):
    """初期変換から、そのまま／アフィンICP後 の候補を返す。

    アフィンICPは縮尺が自由なので、図を縮めて線に乗せる解に落ちうる。
    スケールバーが読めた県では縮尺が5%以上ずれた解を捨てる。
    """
    out = []
    if d0 is not None:
        out.append(("", M0, d0))
    Ma, da = pg.icp(cloud, ref, M0, iters=80)
    if ppk is None or pg.scale_ok(Ma, ppk, lat0):
        out.append(("+アフィン", Ma, da))
    elif d0 is None:  # 比較対象が無いなら縮尺不整合でも一応返す
        out.append(("+アフィン(縮尺不整合)", Ma, da))
    return out


def solve(pdf: Path, pref: str, g: gpd.GeoDataFrame):
    page = fitz.open(pdf)[0]
    labs = pg.labels(page)
    mks = pg.markers(page)
    if not labs or not mks:
        raise ValueError("マーカーまたはラベルが取れない")
    cloud, bbox = pg.boundary_cloud(page)

    muni = g.dissolve("code")
    centroid = {c: (p.representative_point().x, p.representative_point().y)
                for c, p in muni.geometry.items()}
    whole = unary_union(g.geometry.values)
    parts = list(getattr(whole, "geoms", [whole]))
    ref = ring_points([whole])

    # 変換の候補をいくつか作り、「点がラベルの市町村に入るか」で選ぶ。
    # 残差で選ぶと、図を縮めて線の上に乗せた偽解の方が小さくなることがある。
    # 市町村コードはラベルから独立に読めるので、これが唯一の外部基準になる。
    lat0, lon0 = whole.centroid.y, whole.centroid.x
    ppk = pg.scale_pt_per_km(page)
    color_use = pg.use_colors(page)
    mraw = np.array([[m["x"], m["y"]] for m in mks])

    starts = {}
    if ppk is not None:
        # スケールバーで縮尺を固定して平行移動だけ探索する。局所解に落ちない
        Mf, df = pg.fit_fixed_scale(cloud, ref, ppk, lon0, lat0)
        starts[f"縮尺固定 {1 / ppk:.4f}km/pt"] = (Mf, df)
    for name, M0 in (("ラベル重心", pg.init_from_labels(labs, centroid, pref)),
                     ("全体bbox", pg.init_from_bbox(bbox, whole.bounds)),
                     ("最大ポリゴン", pg.init_from_bbox(bbox,
                                                 max(parts, key=lambda p: p.area).bounds))):
        if M0 is not None:
            starts[name] = (M0, None)

    def score(P):
        """この変換での割り当てと、市町村に収まった点の数を返す。"""
        mpts = pg.apply_poly2(P, mraw)
        outside = np.zeros((len(labs), len(mks)))
        for i, lb in enumerate(labs):
            poly = muni.geometry.get(pref + lb["city"])
            if poly is None:
                continue
            for j, (lon, lat) in enumerate(mpts):
                outside[i, j] = poly.distance(Point(lon, lat)) * pg.KM_PER_DEG
        pairs = pg.pair_markers(labs, mks, color_use, outside)
        good = sum(1 for i, j, _c in pairs if outside[i, j] < 1.0)
        return good, pairs

    best = None  # (収まった点数, -残差中央値, 名前, P, pairs)
    for name, (M0, d0) in starts.items():
        for label, M, d in _refine(cloud, ref, M0, d0, ppk, lat0):
            P = np.vstack([M[2], M[0], M[1], np.zeros((3, 2))])
            good, pairs = score(P)
            cand = (good, -float(np.median(d)), f"{name}{label}", P, pairs, d)
            if best is None or cand[:2] > best[:2]:
                best = cand
            # 2次多項式まで進める。元の投影法が不明なぶんを吸収するので、
            # アフィンからの変位は大きくてよい（北海道では0.23度＝約25km動く）。
            # 妥当性の判断は残差ではなく「市町村に収まった点の数」に任せる。
            P2, d2 = pg.refine_poly2(cloud, ref, M, iters=30)
            good2, pairs2 = score(P2)
            cand = (good2, -float(np.median(d2)), f"{name}{label}+2次", P2, pairs2, d2)
            if cand[:2] > best[:2]:
                best = cand
    if best is None:
        raise ValueError("変換を作れない")
    _, _, init, P, pairs, res = best

    pts = np.array([[mks[j]["x"], mks[j]["y"]] for _i, j, _c in pairs])
    ll = pg.apply_poly2(P, pts)

    rows = []
    for (i, _j, cost), (lon, lat) in zip(pairs, ll):
        lb = labs[i]
        code = pref + lb["city"]
        poly = muni.geometry.get(code)
        p = Point(lon, lat)
        inside = None if poly is None else bool(poly.contains(p))
        dist = None if poly is None else round(float(poly.distance(p)) * pg.KM_PER_DEG, 3)
        rows.append(dict(
            PREF=pref, MUNI_CD=code,
            MUNI_NAME="" if poly is None else str(g[g.code == code]["N03_004"].iloc[0]),
            SEQ=lb["seq"], USE=lb["use"],
            TRUTH_LON=round(float(lon), 6), TRUTH_LAT=round(float(lat), 6),
            IN_MUNI=inside, MUNI_DIST_KM=dist, PAIR_PT=round(cost, 1),
            FIT_RES_M=round(float(np.median(res)) * pg.KM_PER_DEG * 1000, 0),
            INIT=init,
        ))
    rows.sort(key=lambda r: (r["MUNI_CD"], r["SEQ"]))
    return rows, float(np.median(res)), len(labs), len(mks)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefs", help="対象県コードをカンマ区切りで指定（既定: PDFがある37県）")
    ap.add_argument("--gpkg", action="store_true", help="QGIS確認用の点GeoPackageも書き出す")
    args = ap.parse_args()

    prefs = args.prefs.split(",") if args.prefs else pdf_prefs()
    print(f"対象: {len(prefs)}都道府県")

    all_rows, bad = [], []
    for pref in prefs:
        print(f"[{pref}]")
        pdf = PDF_DIR / f"{pref}.pdf"
        if not fetch(f"{PDF_BASE}/{pref}.pdf", pdf):
            bad.append((pref, "PDF取得失敗"))
            continue
        try:
            rows, res, nl, nm = solve(pdf, pref, n03(pref))
        except Exception as e:  # noqa: BLE001
            print(f"    失敗: {e}")
            bad.append((pref, str(e)))
            continue
        dists = [r["MUNI_DIST_KM"] for r in rows]
        near = sum(1 for d in dists if d is not None and d < NEAR_KM)
        ratio = near / len(rows) if rows else 0.0
        far = max((d for d in dists if d is not None), default=0.0)
        flag = "" if ratio >= IN_MUNI_MIN else "  ← 不採用"
        print(f"    点{len(rows)} (ラベル{nl}/マーカー{nm})  残差中央値"
              f"{res * pg.KM_PER_DEG * 1000:5.0f}m  市町村内 {near}/{len(rows)}"
              f"  最大はみ出し {far:.2f}km{flag}")
        if ratio < IN_MUNI_MIN:
            bad.append((pref, f"市町村内 {near}/{len(rows)}"))
            continue
        all_rows += rows

    if not all_rows:
        print("採用できた県が無い")
        if bad:
            print("   ", ", ".join(f"{p}({w})" for p, w in bad))
        return
    df = pd.DataFrame(all_rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"\n-> {OUT.relative_to(ROOT)}  {len(df)}点 / {df.PREF.nunique()}都道府県")
    print(f"   市町村ポリゴン内: {df.IN_MUNI.sum()}/{len(df)}"
          f"（外れた点の最大はみ出し {df.MUNI_DIST_KM.max():.2f}km）")
    print(f"   残差中央値の分布: 中央 {df.FIT_RES_M.median():.0f}m / 最大 {df.FIT_RES_M.max():.0f}m")
    if bad:
        print("   採用しなかった県:", ", ".join(f"{p}({w})" for p, w in bad))

    if args.gpkg:
        gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.TRUTH_LON, df.TRUTH_LAT),
                               crs="EPSG:4326")
        gdf.to_file(OUT_GPKG, layer="pdf_truth_2012", driver="GPKG")
        print(f"-> {OUT_GPKG.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
