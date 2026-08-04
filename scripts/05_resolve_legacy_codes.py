#!/usr/bin/env python3
"""track B（平成の大合併前の MUNI_CD）を、県内の町字名マッチで解決する。

これらの行は市区町村名が確定できないため normalize() に渡せない。
そこで Geolonia の全国町字マスタ（master_town.csv）を都道府県単位で引き、
ADR の候補文字列に一致する町字を探す。一致先の市区町村が1つに絞れれば、
それが合併後の自治体であり座標も確定する（＝変遷テーブルを使わずに名寄せできる）。

出力: work/geocode_result_b.jsonl
  status = town_unique   … 県内で一意にマッチ（採用）
           town_ambiguous … 複数市区町村にマッチ（座標は付けない）
           unmatched      … どの候補も一致せず
           no_adr         … ADR が空
"""
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

MASTER = ROOT / "data" / "address_master"
WORK = ROOT / "work"

_KAN = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9}


def kanji_chome_to_arabic(chome: str) -> str:
    """『十二丁目』→『12丁目』。町字マスタは漢数字、台帳は算用数字で持つため揃える。"""
    m = re.match(r"^([〇一二三四五六七八九十]+)丁目$", chome)
    if not m:
        return chome
    s = m.group(1)
    if "十" in s:
        a, _, b = s.partition("十")
        n = (_KAN.get(a, 1) if a else 1) * 10 + (_KAN.get(b, 0) if b else 0)
    else:
        n = sum(_KAN.get(c, 0) for c in s)
    return f"{n}丁目"


def norm_key(s: str) -> str:
    """マッチ用のキー。全角半角・大字/字の有無・空白の差を吸収する。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\s　]+", "", s)
    s = s.replace("大字", "").replace("字", "")
    s = s.replace("ケ", "ヶ").replace("ガ", "ヶ").replace("之", "の").replace("ノ", "の")
    return s


def build_index() -> dict:
    town = pd.read_csv(MASTER / "master_town.csv", dtype=str, keep_default_na=False)
    idx: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for t in town.itertuples():
        if not t.lat or not t.lon:
            continue
        entry = (t.MUNI_CD, f"{t.county}{t.city}{t.ward}", float(t.lon), float(t.lat))
        oaza = t.oaza_cho
        chome_a = kanji_chome_to_arabic(t.chome) if t.chome else ""
        keys = {oaza}
        if t.chome:
            keys |= {oaza + t.chome, oaza + chome_a}
        if t.koaza:
            keys.add(oaza + t.koaza)
        for k in keys:
            k = norm_key(k)
            if k:
                idx[t.pref][k].append(entry)
    return idx


def main() -> None:
    idx = build_index()
    print(f"町字インデックス構築: {sum(len(v) for v in idx.values()):,} キー")

    units = [json.loads(l) for l in (WORK / "geocode_input.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    units = [u for u in units if u["track"] == "B"]
    print(f"track B 照会単位: {len(units):,} 件")

    out = []
    stat = defaultdict(int)
    for u in units:
        pidx = idx.get(u["pref"], {})
        hit = None
        for cand in u["cands"]:
            entries = pidx.get(norm_key(cand))
            if not entries:
                continue
            munis = {e[0] for e in entries}
            if len(munis) == 1:
                # 同一市区町村内で複数の町字に当たることはある（丁目違い等）。先頭を代表とする。
                e = entries[0]
                hit = {"status": "town_unique", "used_cand": cand,
                       "resolved_muni_cd": e[0], "resolved_muni": e[1],
                       "lng": e[2], "lat": e[3], "point_level": 3,
                       "n_match_muni": 1}
                break
            if hit is None:
                hit = {"status": "town_ambiguous", "used_cand": cand,
                       "resolved_muni_cd": "", "resolved_muni": "",
                       "n_match_muni": len(munis),
                       "ambiguous_munis": ",".join(sorted(munis)[:8])}
        if hit is None:
            hit = {"status": "no_adr" if not u["cands"] else "unmatched", "used_cand": ""}
        hit["addr_key"] = u["addr_key"]
        stat[hit["status"]] += 1
        out.append(hit)

    (WORK / "geocode_result_b.jsonl").write_text(
        "\n".join(json.dumps(o, ensure_ascii=False) for o in out) + "\n", encoding="utf-8")

    print("\n照会単位ベースの内訳:")
    for k in ("town_unique", "town_ambiguous", "unmatched", "no_adr"):
        print(f"  {k:16s} {stat[k]:6,d} ({stat[k]/len(units)*100:5.1f}%)")


if __name__ == "__main__":
    main()
