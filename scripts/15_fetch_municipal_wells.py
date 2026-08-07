#!/usr/bin/env python3
"""自治体が公開している井戸データを取得し、F9との突合に使えるかを判定する。

F9の座標（2003年版）は原本の粒度が町名・大字レベルで、実測誤差は中央値300m級ある。
精度を測るには井戸ごとの真位置が要るが、正解になり得るのは
**位置（番地住所または緯度経度）と深度の両方**を公開している自治体だけである。
深度が無いと「F9のこの行と同じ井戸だ」を確定できず、距離を測っても意味が付かない。

横断カタログ3つ（G空間情報センター / BODIK ODCS / 東京都オープンデータカタログ）と
熊本県のデータ連携基盤を、9語（井戸・湧水・揚水・さく井・深井戸・水源井・観測井・
地下水位・水道水源）で検索して見つかった井戸系データセットは21件で、ほぼ全部が
防災用井戸・災害時協力井戸だった。そのうち機械可読なものをここに集める。
カタログ検索そのものは `--search` で再実行できる。

判定の結果（2026-08-07 時点）は README の「他に座標付きの井戸データはあるか」に載せている。
要約すると、**深度を持つのは船橋市だけ**で、これが正解データになっている理由でもある。
"""
import csv
import io
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "municipal"
UA = {"User-Agent": "mlit-groundwater-well-registry/1.0 (+research use)"}

# 横断カタログ。odcs.bodik.jp はポータルで、実体は data.bodik.jp の CKAN。
CATALOGS = [
    ("G空間情報センター", "https://www.geospatial.jp/ckan/api/3/action/package_search"),
    ("BODIK ODCS", "https://data.bodik.jp/api/3/action/package_search"),
    ("東京都オープンデータカタログ", "https://catalog.data.metro.tokyo.lg.jp/api/3/action/package_search"),
]
SEARCH_TERMS = ["井戸", "湧水", "揚水", "さく井", "深井戸", "水源井", "観測井", "地下水位", "水道水源"]

# 見つかった中で機械可読（CSV/シェープ）だったもの。
# name, ファイル名, URL
SOURCES = [
    ("船橋市 防災用井戸一覧", "funabashi.csv",
     "https://www.geospatial.jp/ckan/dataset/c021aacb-ee98-4fd8-acc5-df3d1b148c44/"
     "resource/49ca96d6-8387-40cf-a624-f3552d97c500/download/bousaiyouido.csv"),
    ("千葉市 非常用井戸等", "chiba.csv",
     "https://www.geospatial.jp/ckan/dataset/379b5e14-94a9-4b7a-8907-b4eadf7052be/"
     "resource/10dd4d23-e6dd-4ecf-b1bf-ff750697c139/download/hijouyouidotouitiran291206.csv"),
    ("市川市 防災用井戸", "ichikawa.csv",
     "https://www.geospatial.jp/ckan/dataset/12828f77-6f40-4e36-8efc-8a31390da82f/"
     "resource/4c242739-def9-4e7f-a4c5-ec489c54e413/download/bousaiyouido.csv"),
    ("流山市 災害用井戸設置場所", "nagareyama.csv",
     "https://www.geospatial.jp/ckan/dataset/43b0989d-6683-4e01-99b9-b57c10e3dbbd/"
     "resource/f6322982-1415-4b76-9053-e88bb6143b7b/download/saigaiyouido2.csv"),
    ("東村山市 防災用井戸", "higashimurayama.csv",
     "https://www.geospatial.jp/ckan/dataset/d45c4214-3365-4683-b6ba-a75a10e49a82/"
     "resource/573647d0-ed3d-4dd2-81a3-ee3d8bffba14/download/bosaiyouido.csv"),
    ("相模原市 災害時協力井戸一覧", "sagamihara.csv",
     "https://www.geospatial.jp/ckan/dataset/4579a958-718d-47a5-87de-2d0772b6bd30/"
     "resource/09009f20-6c7d-4dcb-969e-f486644a20d8/download/ido20170428.csv"),
    ("練馬区 防災井戸", "nerima_bousai.csv",
     "https://www.city.nerima.tokyo.jp/kusei/tokei/opendata/opendatasite/bosai/"
     "bousaiido.files/bousaiido.csv"),
    ("練馬区 学校防災井戸", "nerima_gakkou.csv",
     "https://www.city.nerima.tokyo.jp/kusei/tokei/opendata/opendatasite/bosai/"
     "gakkoubousaiido.files/gakkoubousaiido.csv"),
    ("熊本県 地下水位観測地点", "kumamoto_obswell.csv",
     "https://datacatalogportal.dlp-kumamoto.jp/ckan/dataset/103f617d-bd2b-4705-8a14-91f52fc3f3a2/"
     "resource/fecc84f6-530a-4132-bf46-15cbcf710f09/download/observationwell2.csv"),
    # 沖縄の湧水・カー。F9（30m以深の深井戸）とは母集団が違うが、調べた記録として残す。
    ("那覇市 井戸・湧水台帳", "naha_well_spring.zip",
     "https://data.bodik.jp/dataset/7981d41f-486c-4b81-a3bd-a00ce4e1ba0f/"
     "resource/b49c1457-d8df-40f1-a4c5-ad5435efa189/download/472018_well-spring_80941_sanitized.zip"),
]

# 列名から「何を持っているか」を見るための手がかり
DEPTH_HINTS = ("深度", "深さ", "さく井深", "井戸深", "掘削深")
LATLON_HINTS = ("緯度", "経度", "lat", "lon", "location")
ADDR_HINTS = ("住所", "所在地", "番地", "streetAddress")


def fetch(url: str, dest: Path, retries: int = 3) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  skip (既存): {dest.name}")
        return True
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            print(f"  {dest.name}: {len(body):,} bytes")
            return True
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                print(f"  失敗: {url} ({e})")
                return False
            time.sleep(2 * (i + 1))
    return False


def decode(b: bytes) -> str:
    """自治体CSVは CP932 と UTF-8（BOM有無）が混在する。"""
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def norm(s: str) -> str:
    """列名の表記ゆれを吸収する。船橋市の「住　　　所」のように全角スペースが入る。"""
    return re.sub(r"\s+", "", s).lower()


def header_rows(rows: list[list[str]]) -> list[str]:
    """列名を集める。表題行が1行目に来る（流山市）、ヘッダが2段（市川市）などの
    体裁があるので、先頭3行のセルをまとめて「列名の集合」として扱う。"""
    return [norm(c) for r in rows[:3] for c in r]


def has_latlon(rows: list[list[str]]) -> bool:
    """緯度経度が**実際に入っているか**。

    2つの落とし穴を避ける必要がある。
    - 練馬区は緯度・経度の列があるのに値が空。列名だけ見ると「座標あり」になる。
    - 船橋市は深度150.0とポンプ位置44を持つので、行内の数値を無差別に見ると
      経度150度・緯度44度と読めてしまう。

    そこで列名で緯度・経度の列を特定し、その列の値だけを日本の範囲で検査する。
    """
    lat_cols, lon_cols, loc_cols = set(), set(), set()
    for r in rows[:3]:  # ヘッダが2段の体裁（市川市）や表題行つき（流山市）に備える
        for i, c in enumerate(r):
            h = norm(c)
            if h.startswith("緯度") or h == "lat" or h.startswith("latitude"):
                lat_cols.add(i)
            elif h.startswith("経度") or h == "lon" or h.startswith("longitude"):
                lon_cols.add(i)
            elif h == "location":
                loc_cols.add(i)

    def nums(row: list[str], idx: set[int]) -> list[float]:
        out = []
        for i in idx:
            if i >= len(row):
                continue
            for part in row[i].replace(",", " ").split():
                try:
                    out.append(float(part))
                except ValueError:
                    continue
        return out

    for r in rows[1:]:
        got_lat = any(20 <= v <= 46 for v in nums(r, lat_cols | loc_cols))
        got_lon = any(122 <= v <= 154 for v in nums(r, lon_cols | loc_cols))
        if got_lat and got_lon:
            return True
    return False


def has_banchi(rows: list[list[str]]) -> bool:
    """番地レベルの住所があるか。町名止まりでは真位置にならない。

    座標のときと同じ理由で住所の列を先に特定する。全セルを見ると
    熊本県の `uploadDate`（2024-2-28）のような日付を番地と読んでしまう。
    """
    # 「1-2-1」「２０８番地」「3丁目」など。数字と数字の間に区切りが入る形。
    pat = re.compile(r"[0-9０-９]+\s*[-－‐ー の丁目番地]+\s*[0-9０-９]")
    cols = set()
    for r in rows[:3]:
        for i, c in enumerate(r):
            if any(h in norm(c) for h in (norm(x) for x in ADDR_HINTS)):
                cols.add(i)

    def looks_addr(cell: str) -> bool:
        # 市区町村・丁目の字を含み、かつ番地の形をしているセルだけを住所とみなす。
        # これで日付（2024-2-28）や座標を除ける。
        return bool(pat.search(cell)) and any(ch in cell for ch in "市区町村丁目番地")

    # 市川市のCSVはヘッダが2段でデータ行と列がずれており、列名から住所列を特定できない。
    # 値の形からも住所列を拾って補う。
    width = max((len(r) for r in rows), default=0)
    for i in range(width):
        if sum(looks_addr(r[i]) for r in rows[1:] if i < len(r)) >= 3:
            cols.add(i)
    if not cols:
        return False
    hits = sum(bool(pat.search(r[i])) for r in rows[1:] for i in cols if i < len(r))
    return hits >= 3


def has_depth(rows: list[list[str]]) -> bool:
    cols = header_rows(rows)
    return any(norm(h) in " ".join(cols) for h in DEPTH_HINTS)


def survey() -> None:
    """取得済みファイルの中身を見て、F9と突合できるかを判定する。

    判定基準は「位置（番地住所か緯度経度）＋深度」。深度が無いとF9のどの行と
    同じ井戸なのかを確定できず、距離を測っても精度の実測値にならない。
    """
    print("\n=== 突合に使えるか（位置＋深度が要る） ===")
    print(f"{'自治体・データ名':30s}{'件数':>6s}  {'座標':>4s}{'番地':>4s}{'深度':>4s}  判定")
    for name, fn, _ in SOURCES:
        p = RAW / fn
        if not p.exists():
            print(f"{name:30s}{'-':>6s}  未取得")
            continue
        if p.suffix == ".zip":
            print(f"{name:30s}{'-':>6s}  （シェープ。母集団が違うため判定対象外）")
            continue
        rows = list(csv.reader(io.StringIO(decode(p.read_bytes()))))
        n = max(len(rows) - 1, 0)
        c_ll, c_ad, c_dp = has_latlon(rows), has_banchi(rows), has_depth(rows)
        verdict = "★正解データに使える" if c_dp and (c_ll or c_ad) else "深度が無く同定できない"
        m = lambda b: "  ○ " if b else "  − "  # noqa: E731
        print(f"{name:30s}{n:>6d}  {m(c_ll)}{m(c_ad)}{m(c_dp)}  {verdict}")


def search() -> None:
    """横断カタログを検索し直す（データセットが増えていないかの確認用）。"""
    seen: dict[tuple[str, str], str] = {}
    for term in SEARCH_TERMS:
        for cname, base in CATALOGS:
            url = f"{base}?q={urllib.parse.quote(term)}&rows=30"
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=60) as r:
                    res = json.load(r)["result"]
            except Exception as e:  # noqa: BLE001
                print(f"  {cname} / {term}: 失敗 ({e})")
                continue
            for pkg in res["results"]:
                title = pkg["title"]
                if not any(k in title for k in ("井戸", "湧水", "揚水", "さく井", "水源", "観測井", "地下水位")):
                    continue
                org = (pkg.get("organization") or {}).get("title", "")
                fmts = sorted({x.get("format", "") for x in pkg.get("resources", []) if x.get("format")})
                seen[(org, title)] = f"{'/'.join(fmts)} ({cname})"
    print(f"\n井戸系データセット {len(seen)}件")
    for (org, title), meta in sorted(seen.items()):
        print(f"  [{org[:12]:12s}] {title[:52]:52s} {meta}")


# 属性の拾い方。列名は自治体ごとにばらばらなので、位置以外はこの手がかりで拾う。
NAME_HINTS = ("名称", "施設名", "設置場所", "設置施設名称", "イベントの名称", "name")
# 住所しか無く、ジオコーディングが要る自治体（都道府県名を補ってから引く）
GEOCODE_PREFIX = {"funabashi.csv": "千葉県船橋市", "nerima_bousai.csv": "", "nerima_gakkou.csv": ""}
GSI_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch?q="


def geocode(address: str) -> tuple[float, float] | None:
    """国土地理院の住所検索APIで番地まで解決する（13_eval_2003_funabashi.py と同じ経路）。"""
    try:
        req = urllib.request.Request(GSI_URL + urllib.parse.quote(address), headers=UA)
        with urllib.request.urlopen(req, timeout=30) as f:
            hits = json.load(f)
    except Exception:  # noqa: BLE001
        return None
    if not hits:
        return None
    lon, lat = hits[0]["geometry"]["coordinates"]
    return lon, lat


def col_index(rows: list[list[str]], hints: tuple[str, ...]) -> int | None:
    """列名から列の位置を探す。ヘッダが2段の体裁があるので先頭3行を見る。"""
    for r in rows[:3]:
        for i, c in enumerate(r):
            h = norm(c)
            if any(norm(x) in h for x in hints):
                return i
    return None


def addr_index(rows: list[list[str]]) -> int | None:
    """住所の列を選ぶ。列名だけで選ぶと練馬区の「所在地_全国地方公共団体コード」
    （値は 131202）を拾ってしまうので、住所らしい値がいちばん多い列を採る。"""
    pat = re.compile(r"[0-9０-９]+\s*[-－‐ー の丁目番地]+\s*[0-9０-９]")

    def score(i: int) -> int:
        return sum(
            1 for r in rows[1:]
            if i < len(r) and pat.search(r[i]) and any(ch in r[i] for ch in "市区町村丁目番地")
        )

    width = max((len(r) for r in rows), default=0)
    best, best_score = None, 0
    for i in range(width):
        s = score(i)
        if s > best_score:
            best, best_score = i, s
    if best is not None:
        return best
    return col_index(rows, ADDR_HINTS)


def features_of(fn: str, rows: list[list[str]]) -> list[dict]:
    """1ファイルを GeoJSON のフィーチャ列にする。

    座標を持つものはその値を使い、住所しか無いものは番地までジオコーディングする。
    どちらで得た座標かは `POS_SOURCE` に残す。混ぜたまま渡すと、
    ジオコーディング由来の点を原本の実測座標と誤解されるため。
    """
    lat_i = col_index(rows, ("緯度", "lat"))
    lon_i = col_index(rows, ("経度", "lon"))
    loc_i = col_index(rows, ("location",))
    name_i = col_index(rows, NAME_HINTS)
    addr_i = addr_index(rows)
    depth_i = col_index(rows, DEPTH_HINTS)

    def cell(r: list[str], i: int | None) -> str:
        return r[i].strip() if i is not None and i < len(r) else ""

    feats = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        lon = lat = None
        if loc_i is not None and cell(r, loc_i):
            parts = cell(r, loc_i).split(",")
            if len(parts) == 2:
                try:
                    lat, lon = float(parts[0]), float(parts[1])
                except ValueError:
                    lat = lon = None
        if lat is None and lat_i is not None and lon_i is not None:
            try:
                lat, lon = float(cell(r, lat_i)), float(cell(r, lon_i))
            except ValueError:
                lat = lon = None
        src = "原本の座標"

        addr = cell(r, addr_i)
        if (lat is None or lon is None) and fn in GEOCODE_PREFIX and addr:
            hit = geocode(GEOCODE_PREFIX[fn] + addr)
            time.sleep(0.5)  # 地理院APIへの連投を避ける
            if hit is None:
                continue
            lon, lat = hit
            src = "住所からジオコーディング"
        if lat is None or lon is None:
            continue
        if not (20 <= lat <= 46 and 122 <= lon <= 154):
            continue

        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {
                "SOURCE_FILE": fn,
                "NAME": cell(r, name_i),
                "ADDRESS": addr,
                "DEPTH_M": cell(r, depth_i),
                "POS_SOURCE": src,
            },
        })
    return feats


def to_geojson() -> None:
    """取得済みCSVを1本の GeoJSON にまとめる。QGIS でF9の点と重ねて見るため。"""
    out = ROOT / "output" / "municipal_wells.geojson"
    feats: list[dict] = []
    for name, fn, _ in SOURCES:
        p = RAW / fn
        if not p.exists() or p.suffix == ".zip":
            continue
        rows = list(csv.reader(io.StringIO(decode(p.read_bytes()))))
        got = features_of(fn, rows)
        srcs = {f["properties"]["POS_SOURCE"] for f in got}
        print(f"  {name:30s} {len(got):>4d}点  {'/'.join(sorted(srcs))}")
        feats += got
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"\n  -> {out}（{len(feats)}点 / {out.stat().st_size:,} bytes）")


def main() -> None:
    import sys

    if "--search" in sys.argv:
        search()
        return
    if "--geojson" in sys.argv:
        print("GeoJSON を作る")
        to_geojson()
        return
    print(f"取得先: {RAW}")
    ok = ng = 0
    for name, fn, url in SOURCES:
        print(f"{name}:")
        if fetch(url, RAW / fn):
            ok += 1
        else:
            ng += 1
    print(f"\n完了: 成功 {ok} / 失敗 {ng}")
    survey()


if __name__ == "__main__":
    main()
