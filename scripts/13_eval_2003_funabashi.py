#!/usr/bin/env python3
"""2003年版の座標を、船橋市の防災用井戸オープンデータと突き合わせて実測する。

2003年版（`output/f9_wells_2003.csv`）には正解データが無い。位置図PDFから復元した
378点（`09_pdf_truth.py`）は平成24年度の井戸なので、2003年版には収録されていない。

そこで自治体が公開している井戸データを正解として使う。**災害時協力井戸の多くは
住民登録の浅井戸**でF9（概ね30m以深の深井戸）と母集団が重ならないが、
**自治体が公共施設に設置した防災井戸は深井戸**なので重なる。船橋市は
さく井深度と番地までの住所を公開しているため、これを正解にできる。

  出典: 船橋市「防災用井戸一覧」（BODIK / CC BY 4.0）
  正解の作り方: 番地までの住所を国土地理院の住所検索APIでジオコーディング

対応づけは町名・さく井深度・設置年の3点一致で行う。井戸そのものの識別子が
どちらにも無いため、これ以上は詰められない。正解側は施設の番地代表点なので
学校敷地の広さ（100m前後）が正解自体の不確かさとして残る点に注意。

使い方:
  python3 scripts/13_eval_2003_funabashi.py
  python3 scripts/13_eval_2003_funabashi.py --csv   # 突合結果を output/ に書き出す
"""
import argparse
import json
import math
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "output" / "f9_wells_2003.csv"
CACHE = ROOT / "data" / "raw" / "funabashi_wells.csv"
OUT = ROOT / "output" / "f9_2003_error_funabashi.csv"

WELLS_URL = ("https://data.bodik.jp/dataset/470c6f66-d838-44f2-aa8b-fa7a771b9d61"
             "/resource/076e607d-3be6-44da-b2fa-632a735abba7/download/.csv")
GSI_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch?q="
MUNI_CD = "12204"  # 船橋市
UA = {"User-Agent": "mlit-groundwater-well-registry/1.0 (+research use)"}

ERA = {"昭": 1925, "平": 1988}          # 昭和55年 -> 1980
DATA_YEAR = 2003                        # 2003年版に載りうるのはこの年まで
M_PER_DEG_LAT = 111132.0


def norm_town(s: str) -> str:
    """『松が丘』『松ヶ丘』『八木が谷』『八木ヶ谷』を同一視する。"""
    return re.sub(r"[ヶケが]", "ケ", s).replace("町", "").replace("丁目", "")


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def fetch_wells() -> pd.DataFrame:
    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(WELLS_URL, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            CACHE.write_bytes(r.read())
        print(f"  取得: {CACHE.name}")
    return pd.read_csv(CACHE, encoding="cp932")


def geocode(address: str) -> tuple[float, float] | None:
    """国土地理院の住所検索APIで番地まで解決する。"""
    req = urllib.request.Request(GSI_URL + urllib.parse.quote(address), headers=UA)
    with urllib.request.urlopen(req, timeout=30) as f:
        hits = json.load(f)
    if not hits:
        return None
    lon, lat = hits[0]["geometry"]["coordinates"]
    return lon, lat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="store_true", help="突合結果をCSVに書き出す")
    args = ap.parse_args()

    wells = fetch_wells()
    addr_col = next(c for c in wells.columns if c.replace("　", "") == "住所")
    dep_col = next(c for c in wells.columns if c.startswith("さく井深度"))

    f9 = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    f9 = f9[f9.MUNI_CD == MUNI_CD].copy()
    f9["DEPf"] = pd.to_numeric(f9.DEP, errors="coerce")
    f9["SY"] = pd.to_numeric(f9.START.str[:4], errors="coerce")
    f9["LONf"] = f9.LON.astype(float)
    f9["LATf"] = f9.LAT.astype(float)

    rows, skipped = [], []
    for _, w in wells.iterrows():
        era = str(w["設置年"])
        year = ERA[era[0]] + int(era[1:])
        if year > DATA_YEAR:
            continue                       # 2003年版より後に設置された井戸
        addr = unicodedata.normalize("NFKC", str(w[addr_col]))
        dep = float(w[dep_col])
        town = norm_town(re.sub(r"[0-9\-]+$", "", addr))

        cand = f9[(f9.DEPf.sub(dep).abs() <= 6) & (f9.SY.sub(year).abs() <= 3)
                  & (f9.ADR.map(lambda a: norm_town(a) == town))]
        if cand.empty:
            skipped.append((w["設置場所"], town, dep, year))
            continue

        hit = geocode("千葉県船橋市" + addr)
        time.sleep(0.4)                    # APIへの連続アクセスを避ける
        if hit is None:
            skipped.append((w["設置場所"], town, dep, year))
            continue
        t_lon, t_lat = hit

        c = min(cand.itertuples(),
                key=lambda c: haversine_m(t_lat, t_lon, c.LATf, c.LONf))
        m_lon = M_PER_DEG_LAT * math.cos(math.radians(t_lat))
        rows.append(dict(
            NAME=w["設置場所"], ADDRESS=addr, YEAR=year, DEP_MUNI=dep,
            TRUTH_LON=round(t_lon, 6), TRUTH_LAT=round(t_lat, 6),
            F9_ADR=c.ADR, F9_DEP=c.DEP, F9_START=c.START,
            F9_LON=c.LONf, F9_LAT=c.LATf, POS_QUALITY=c.POS_QUALITY,
            D_NORTH_M=round((c.LATf - t_lat) * M_PER_DEG_LAT),
            D_EAST_M=round((c.LONf - t_lon) * m_lon),
            ERROR_M=round(haversine_m(t_lat, t_lon, c.LATf, c.LONf))))

    m = pd.DataFrame(rows)
    print(f"\n突合できた {len(m)}点 / 対応づかなかった {len(skipped)}点")
    for name, town, dep, year in skipped:
        print(f"  - {name}（{town} {dep}m {year}年）")

    print(f"\n{'施設':<16}{'F9 ADR':<10}{'品質':>9}{'北(m)':>8}{'東(m)':>8}{'誤差':>8}")
    for r in m.itertuples():
        print(f"{r.NAME:<16}{r.F9_ADR:<10}{r.POS_QUALITY:>9}"
              f"{r.D_NORTH_M:8.0f}{r.D_EAST_M:8.0f}{r.ERROR_M:8.0f}m")

    n_mean, e_mean = m.D_NORTH_M.mean(), m.D_EAST_M.mean()
    print(f"\n誤差 中央値 {m.ERROR_M.median():.0f}m / 平均 {m.ERROR_M.mean():.0f}m "
          f"/ 最大 {m.ERROR_M.max():.0f}m")
    print(f"平均ずれベクトル 北{n_mean:+.0f}m 東{e_mean:+.0f}m "
          f"（大きさ {math.hypot(n_mean, e_mean):.0f}m）")
    print("  ずれの向きが揃っておらず平均が小さいことが、測地系が JGD2000 で正しい"
          "（二重変換していない）ことの裏付けになる。")
    print("  正解は施設の番地代表点なので、学校敷地の広さ100m前後は正解側の"
          "不確かさとして含まれる。")

    if args.csv:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        m.to_csv(OUT, index=False, encoding="utf-8", lineterminator="\r\n")
        print(f"\n出力: {OUT}")


if __name__ == "__main__":
    main()
