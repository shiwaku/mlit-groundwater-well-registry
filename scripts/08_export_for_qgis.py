#!/usr/bin/env python3
"""QGIS で位置を目視検証するための GeoPackage を書き出す。

GeoParquet ではなく GeoPackage にするのは、QGIS がレイヤ名・属性型・空間インデックスを
そのまま扱えて、検証作業の取り回しが良いため。

付与する検証用の列:
  GC_STACK_N    同一座標に重なっている井戸の件数。
                町字レベルでも同じ町字の井戸は同一点になるため、これが大きい点は
                「1本の井戸の位置」ではなく「その町字にN本ある」という意味になる。
                市区町村フォールバックでは金沢市役所に429件重なる例がある。
  GC_DEM        国土地理院DEMの標高（--dem 指定時のみ）
  GC_DEM_DIFF   HEIGHT（台帳の地盤標高）との差の絶対値。大きい行は位置が怪しい

レイヤ:
  wells_all    座標が付いた全行
  wells_high   GC_CONFIDENCE=high のみ（個別井戸の位置として使える候補）
  wells_check  検証優先度が高い行（DEM差が大きい、または重なりが少なく信頼度が低い）

使い方:
  python3 scripts/08_export_for_qgis.py                    # DEM照会なし（すぐ終わる）
  python3 scripts/08_export_for_qgis.py --dem 2000         # 2000点だけDEM照会
  python3 scripts/08_export_for_qgis.py --dem 0 --level town  # town限定で全点照会
"""
import argparse
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "output" / "f9_groundwater_all_geocoded.csv"
OUT = ROOT / "output" / "f9_groundwater_wells_qgis.gpkg"

DEM_API = "https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php"
UA = {"User-Agent": "mlit-groundwater-well-registry/1.0 (position verification)"}
NUMERIC_SAFE = ["HEIGHT", "DEP", "SC", "SCL", "Q_01", "QSP_01", "TEMP", "PH"]


def elevation(lonlat):
    lon, lat = lonlat
    url = f"{DEM_API}?lon={lon}&lat={lat}&outtype=JSON"
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                v = json.loads(r.read().decode()).get("elevation")
            if isinstance(v, (int, float)):
                return float(v)
        except Exception:  # noqa: BLE001  一時的な失敗はリトライ
            pass
    return np.nan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", type=int, default=None,
                    help="DEMを照会する点数。0 で全点。未指定なら照会しない")
    ap.add_argument("--level", default=None, choices=["town", "oaza", "city"],
                    help="DEM照会をこの精度レベルに限定する")
    ap.add_argument("--workers", type=int, default=4, help="DEM照会の並列数")
    args = ap.parse_args()

    df = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    g = df[(df.GC_LAT != "") & (df.GC_LON != "")].copy()
    g["lon"] = g.GC_LON.astype(float)
    g["lat"] = g.GC_LAT.astype(float)

    # 同一座標に何本の井戸が重なっているか
    key = g.GC_LAT + "," + g.GC_LON
    g["GC_STACK_N"] = key.map(key.value_counts()).astype("Int32")

    for c in NUMERIC_SAFE:
        g[c] = pd.to_numeric(g[c].replace("", None), errors="coerce")
    g["GC_SPREAD_KM"] = pd.to_numeric(g.GC_SPREAD_KM.replace("", None), errors="coerce")
    g["GC_N_TOWNS"] = pd.to_numeric(g.GC_N_TOWNS.replace("", None), errors="coerce").astype("Int32")

    g["GC_DEM"] = np.nan
    if args.dem is not None:
        t = g if args.level is None else g[g.GC_LEVEL == args.level]
        t = t[t.HEIGHT.notna()]                      # HEIGHT が無いと比較できない
        uniq = t.drop_duplicates(subset=["GC_LAT", "GC_LON"])  # 同一座標は1回だけ引く
        if args.dem > 0 and len(uniq) > args.dem:
            uniq = uniq.sample(args.dem, random_state=42)
        print(f"DEM照会: {len(uniq):,} 点（重複座標は集約済み）…")
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            dem = list(ex.map(elevation, zip(uniq.lon, uniq.lat)))
        m = dict(zip(uniq.GC_LAT + "," + uniq.GC_LON, dem))
        g["GC_DEM"] = (g.GC_LAT + "," + g.GC_LON).map(m)
        got = g.GC_DEM.notna().sum()
        print(f"  取得成功: {len(uniq) - sum(np.isnan(dem)):,} 点 → {got:,} 行に反映")

    g["GC_DEM_DIFF"] = (g.HEIGHT - g.GC_DEM).abs()

    gdf = gpd.GeoDataFrame(
        g.drop(columns=["X", "Y", "lon", "lat"]),
        geometry=[Point(x, y) for x, y in zip(g.lon, g.lat)],
        crs="EPSG:4326",
    )

    # 検証優先度が高い行: DEM差が大きい / 重心の広がりが大きい / low信頼度で単独点
    check = gdf[
        (gdf.GC_DEM_DIFF > 50)
        | (gdf.GC_SPREAD_KM > 1)
        | ((gdf.GC_CONFIDENCE == "low") & (gdf.GC_LEVEL != "city"))
    ]

    layers = {
        "wells_all": gdf,
        "wells_high": gdf[gdf.GC_CONFIDENCE == "high"],
        "wells_check": check,
    }
    if OUT.exists():
        OUT.unlink()
    for name, layer in layers.items():
        layer.to_file(OUT, layer=name, driver="GPKG")
        print(f"  レイヤ {name:12s} {len(layer):7,d} フィーチャ")
    print(f"\n出力: {OUT}  ({OUT.stat().st_size/1024/1024:.1f} MB)")

    print("\n同一座標への重なり:")
    s = gdf.GC_STACK_N
    print(f"  単独点の行: {(s == 1).sum():,} / 10件以上重なる点の行: {(s >= 10).sum():,}")
    print(f"  最大: {s.max()} 件（この点は個別井戸の位置ではなく市区町村代表点）")


if __name__ == "__main__":
    main()
