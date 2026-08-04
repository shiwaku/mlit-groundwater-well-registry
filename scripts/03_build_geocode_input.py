#!/usr/bin/env python3
"""マージ済みCSVから、ジオコーディング用の入力を組み立てる。

台帳の ADR には都道府県名も市区町村名も入っていないため、MUNI_CD から
自治体名を引いて住所文字列を合成する。MUNI_CD が現行コード表に無い行
（平成の大合併前のコード）は市区町村名が確定できないので track=B に分け、
別途「県内の町字名マッチ」で解決する。

出力:
  geocode_input.jsonl  … 1行 = ユニークな住所（addr_key）単位の照会単位
  rows_index.csv       … 台帳の各行 -> addr_key の対応
"""
import csv
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from adr_norm import candidates  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "data" / "address_master"
WORK = ROOT / "work"
SRC = ROOT / "output" / "f9_groundwater_all.csv"


def main() -> None:
    df = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    city = pd.read_csv(MASTER / "master_city.csv", dtype=str, keep_default_na=False)
    cmap = {
        r.MUNI_CD: (r.pref, r.county, r.city, r.ward, r.lon, r.lat)
        for r in city.itertuples()
    }
    # PREF -> 都道府県名（旧コード行でも都道府県は必ず分かる）
    pref_by_cd = {}
    for r in city.itertuples():
        pref_by_cd.setdefault(r.MUNI_CD[:2], r.pref)

    units: dict[str, dict] = {}
    rows = []
    stat = {"A": 0, "B": 0, "no_pref": 0, "no_adr_A": 0, "no_adr_B": 0}

    for t in df.itertuples():
        muni = t.MUNI_CD
        pref2 = muni[:2] if muni else t.PREF.zfill(2)
        pref = pref_by_cd.get(pref2, "")
        if not pref:
            stat["no_pref"] += 1
            rows.append({"i": t.Index, "addr_key": ""})
            continue

        info = cmap.get(muni)
        track = "A" if info else "B"
        stat[track] += 1

        if info:
            _, county, cty, ward, _, _ = info
            muni_full = f"{county}{cty}{ward}"
        else:
            muni_full = ""

        cands = candidates(t.ADR)
        # 政令市の行政区名が ADR 側にも入っている場合の二重化を解消
        if info and ward:
            cands = [c[len(ward):].strip() if c.startswith(ward) else c for c in cands]
            cands = [c for c in dict.fromkeys(cands) if c]

        if not cands:
            stat["no_adr_" + track] += 1

        key = f"{track}|{muni}|{muni_full}|{'/'.join(cands)}"
        if key not in units:
            units[key] = {
                "addr_key": key,
                "track": track,
                "muni_cd": muni,
                "pref": pref,
                "muni_full": muni_full,
                "cands": cands,
            }
        rows.append({"i": t.Index, "addr_key": key})

    with (WORK / "geocode_input.jsonl").open("w", encoding="utf-8") as fp:
        for u in units.values():
            fp.write(json.dumps(u, ensure_ascii=False) + "\n")
    with (WORK / "rows_index.csv").open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=["i", "addr_key"])
        w.writeheader()
        w.writerows(rows)

    a = sum(1 for u in units.values() if u["track"] == "A")
    b = len(units) - a
    print(f"台帳 {len(df):,} 行")
    print(f"  track A（現行コード・市区町村名あり）: {stat['A']:,} 行  うち ADR 空 {stat['no_adr_A']:,}")
    print(f"  track B（合併前コード・要名寄せ）    : {stat['B']:,} 行  うち ADR 空 {stat['no_adr_B']:,}")
    print(f"  都道府県すら不明                     : {stat['no_pref']:,} 行")
    print(f"\n照会単位（重複排除後）: {len(units):,} 件  (A={a:,} / B={b:,})")
    print(f"  → API 呼び出しは最大 {len(units):,} 件に圧縮（元は {len(df):,} 行）")


if __name__ == "__main__":
    main()
