#!/usr/bin/env python3
"""ジオコーディング結果を精緻化し、精度フラグ付きの最終CSVを出力する。

前段（04/05）で取り切れなかった分を、Geolonia の全国町字マスタへの直接マッチで拾う。

  track A の市区町村止まり
      → 同一市区町村内で「大字名の完全一致／前方一致」する町字群の重心を採る。
        例『篠路』は篠路1〜9条ほか87町字に前方一致するので、その重心を地区代表点とする。
  track B の未解決
      → 現在の大字名が「旧自治体名＋旧大字名」になっている前提で後方一致を試す。
        例『聚富』→『厚田区聚富』。前置部分が1文字だと『二股』→『下二股』のような
        誤マッチになるため、前置は2文字以上を要求する。

出力 output/f9_groundwater_all_geocoded.csv の付与列:
  GC_LAT / GC_LON      緯度経度（EPSG:4326）
  GC_LEVEL             town = 町丁目 / oaza = 大字・地区の重心 / city = 市区町村代表点 / none = 付与不可
  GC_METHOD            どの経路で決まったか
  GC_ADDR_USED         実際に照合に使った住所文字列
  GC_MUNI_CD_RESOLVED  名寄せ後の現行市区町村コード（track B のみ意味を持つ）
  GC_MUNI_RESOLVED     名寄せ後の市区町村名
  GC_N_TOWNS           重心を取った町字の件数（GC_LEVEL=oaza のとき）
  GC_CONFIDENCE        high / medium / low（下記 CONFIDENCE の定義を参照）
  GC_SPREAD_KM         重心を取った町字群の広がり（重心からの二乗平均距離・km）。
                       この値が大きい行は位置が粗い。GC_LEVEL=oaza のときに入る。
"""
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "data" / "address_master"
WORK = ROOT / "work"
OUT = ROOT / "output" / "f9_groundwater_all_geocoded.csv"

MIN_PREFIX = 2  # 後方一致で要求する前置（旧自治体名相当）の最小文字数

# 経路ごとの信頼度。
#   high   … 正規化エンジンが町字を確定させた。市区町村名が原本のコードから
#             一意に引けているので、取り違えの余地がない。
#   medium … 市区町村は確定しているが町字は大字・地区の重心（A_oaza_*）、
#             または旧コードを県内で一意な大字名から名寄せした（B_town_exact）。
#             後者は変遷テーブルによる確定ではなく名称一致による推定である。
#   low    … 市区町村代表点まで（A_city_fallback）、または旧コードを
#             『旧自治体名＋大字名』の後方一致で推定した（B_town_suffix）。
#
# 格付けは実測に基づく。国土地理院DEMの標高と台帳の HEIGHT（2万5千分の1地形図
# からの読み取り値＝独立系列）を1,203点で突き合わせ、起伏の交絡を避けるため
# 低地（DEM 100m以下・939点）に絞って比較した結果が下記。
#
#   経路              標高差 中央値 / 90%ile / 50m超率
#   A_oaza_exact         2.0m /  16.8m /  0.6%
#   A_normalize          2.7m /  24.8m /  4.5%
#   A_city_fallback      3.0m /  37.5m /  5.7%
#   B_town_exact         3.4m /  34.2m /  5.7%
#   B_town_suffix        4.1m /  43.3m /  8.8%
#   A_oaza_prefix        9.9m /  47.3m /  8.8%   <- exact より明らかに劣る
#
# A_oaza_prefix は当初 medium としていたが、上記に加えて重心を取った町字群の
# 広がりが最大17kmに達する例があることから low に落とした。
CONFIDENCE = {
    "A_normalize": "high",
    "A_oaza_exact": "medium",
    "B_town_exact": "medium",
    "A_city_fallback": "low",
    "A_oaza_prefix": "low",
    "B_town_suffix": "low",
}


def nk(s: str) -> str:
    """照合用キー。全角半角・大字/字の有無・送り仮名の差を吸収する。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\s　]+", "", s)
    s = s.replace("大字", "").replace("字", "")
    s = s.replace("通り", "通").replace("ケ", "ヶ").replace("ガ", "ヶ")
    s = s.replace("之", "の").replace("ノ", "の")
    return s


def load_town_master():
    t = pd.read_csv(MASTER / "master_town.csv", dtype=str, keep_default_na=False)
    t = t[(t.lat != "") & (t.lon != "")]
    by_muni = defaultdict(list)   # MUNI_CD -> [(key_full, key_oaza, lon, lat)]
    by_pref = defaultdict(list)   # pref    -> [(MUNI_CD, muni_full, key_oaza, lon, lat)]
    for r in t.itertuples():
        ko, kf = nk(r.oaza_cho), nk(r.oaza_cho + r.chome)
        lon, lat = float(r.lon), float(r.lat)
        by_muni[r.MUNI_CD].append((kf, ko, lon, lat))
        by_pref[r.pref].append((r.MUNI_CD, f"{r.county}{r.city}{r.ward}", ko, lon, lat))
    return by_muni, by_pref


def centroid(pts):
    """重心と、重心からの二乗平均距離（km）を返す。後者が位置の不確かさの目安になる。"""
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    if n == 1:
        return cx, cy, 0.0
    latr = math.radians(cy)
    sq = 0.0
    for x, y in pts:
        dx = (x - cx) * 111.320 * math.cos(latr)
        dy = (y - cy) * 110.574
        sq += dx * dx + dy * dy
    return cx, cy, math.sqrt(sq / n)


def refine_a(unit, by_muni):
    """track A の市区町村止まりを大字・地区レベルへ引き上げる。"""
    rows = by_muni.get(unit["muni_cd"], [])
    if not rows:
        return None
    for cand in unit["cands"]:
        k = nk(cand)
        if len(k) < 2:
            continue
        for mode in ("exact", "prefix"):
            if mode == "exact":
                hit = [r for r in rows if r[0] == k or r[1] == k]
            else:
                hit = [r for r in rows if r[0].startswith(k) or r[1].startswith(k)]
            if hit:
                lon, lat, rms = centroid([(r[2], r[3]) for r in hit])
                return {"lat": lat, "lng": lon, "level": "oaza",
                        "method": f"A_oaza_{mode}", "used": cand,
                        "n": len(hit), "spread": rms}
    return None


def refine_b(unit, by_pref):
    """track B の未解決を『旧自治体名＋旧大字名』の後方一致で拾う。"""
    rows = by_pref.get(unit["pref"], [])
    for cand in unit["cands"]:
        k = nk(cand)
        if len(k) < 2:
            continue
        hit = [r for r in rows
               if r[2].endswith(k) and len(r[2]) - len(k) >= MIN_PREFIX]
        if not hit:
            continue
        munis = {r[0] for r in hit}
        if len(munis) != 1:
            continue  # 県内の複数自治体に該当。特定できないので採らない
        sub = [r for r in hit if r[0] == hit[0][0]]
        lon, lat, rms = centroid([(r[3], r[4]) for r in sub])
        return {"lat": lat, "lng": lon,
                "level": "town" if len(sub) == 1 else "oaza",
                "method": "B_town_suffix", "used": cand,
                "muni_cd": hit[0][0], "muni": hit[0][1],
                "n": len(sub), "spread": rms}
    return None


def main() -> None:
    by_muni, by_pref = load_town_master()
    units = {}
    for line in (WORK / "geocode_input.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            u = json.loads(line)
            units[u["addr_key"]] = u

    # 照会単位ごとの最終解を組み立てる
    resolved: dict[str, dict] = {}

    for line in (WORK / "geocode_result_a.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        u = units[d["addr_key"]]
        if d["status"] == "town":
            resolved[d["addr_key"]] = {
                "lat": d["lat"], "lng": d["lng"], "level": "town",
                "method": "A_normalize", "used": d["used_cand"],
                "muni_cd": u["muni_cd"], "muni": u["muni_full"], "n": 1}
        elif d["status"] == "city":
            up = refine_a(u, by_muni)
            if up:
                up |= {"muni_cd": u["muni_cd"], "muni": u["muni_full"]}
                resolved[d["addr_key"]] = up
            else:
                resolved[d["addr_key"]] = {
                    "lat": d["lat"], "lng": d["lng"], "level": "city",
                    "method": "A_city_fallback", "used": d["used_cand"],
                    "muni_cd": u["muni_cd"], "muni": u["muni_full"], "n": 1}
        else:
            resolved[d["addr_key"]] = {"level": "none", "method": "A_failed", "used": ""}

    for line in (WORK / "geocode_result_b.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        u = units[d["addr_key"]]
        if d["status"] == "town_unique":
            resolved[d["addr_key"]] = {
                "lat": d["lat"], "lng": d["lng"], "level": "town",
                "method": "B_town_exact", "used": d["used_cand"],
                "muni_cd": d["resolved_muni_cd"], "muni": d["resolved_muni"], "n": 1}
            continue
        up = refine_b(u, by_pref)
        if up:
            resolved[d["addr_key"]] = up
        else:
            resolved[d["addr_key"]] = {
                "level": "none",
                "method": "B_" + d["status"],
                "used": d.get("used_cand", "")}

    # 台帳の各行へ展開
    df = pd.read_csv(ROOT / "output" / "f9_groundwater_all.csv", dtype=str, keep_default_na=False)
    ridx = pd.read_csv(WORK / "rows_index.csv", dtype=str, keep_default_na=False)
    keys = ridx.set_index("i")["addr_key"].to_dict()

    cols = defaultdict(list)
    for i in range(len(df)):
        r = resolved.get(keys.get(str(i), ""), {"level": "none", "method": "no_pref", "used": ""})
        cols["GC_LAT"].append(f"{r['lat']:.6f}" if r.get("lat") is not None and "lat" in r else "")
        cols["GC_LON"].append(f"{r['lng']:.6f}" if r.get("lng") is not None and "lng" in r else "")
        cols["GC_LEVEL"].append(r["level"])
        cols["GC_METHOD"].append(r["method"])
        cols["GC_ADDR_USED"].append(r.get("used", ""))
        cols["GC_MUNI_CD_RESOLVED"].append(r.get("muni_cd", ""))
        cols["GC_MUNI_RESOLVED"].append(r.get("muni", ""))
        cols["GC_N_TOWNS"].append(str(r.get("n", "")) if r.get("n") else "")
        cols["GC_CONFIDENCE"].append(CONFIDENCE.get(r["method"], ""))
        sp = r.get("spread")
        cols["GC_SPREAD_KM"].append(f"{sp:.3f}" if sp is not None else "")
    for k, v in cols.items():
        df[k] = v

    df.to_csv(OUT, index=False, encoding="utf-8", lineterminator="\r\n")

    # 集計
    n = len(df)
    print(f"出力: {OUT}  ({n:,} 行 × {len(df.columns)} 列)\n")
    lv = Counter(df.GC_LEVEL)
    print("精度レベル別の行数:")
    labels = {"town": "町丁目レベル", "oaza": "大字・地区レベル（重心）",
              "city": "市区町村レベル（代表点）", "none": "座標なし"}
    for k in ("town", "oaza", "city", "none"):
        print(f"  {labels[k]:26s} {lv[k]:7,d} ({lv[k]/n*100:5.1f}%)")
    coded = n - lv["none"]
    print(f"\n  座標付与できた行: {coded:,} ({coded/n*100:.1f}%)")
    print("\n信頼度別:")
    cf = Counter(df.GC_CONFIDENCE)
    for k in ("high", "medium", "low", ""):
        label = k or "（座標なし）"
        print(f"  {label:12s} {cf[k]:7,d} ({cf[k]/n*100:5.1f}%)")
    print("\n経路別:")
    for k, v in Counter(df.GC_METHOD).most_common():
        print(f"  {k:20s} {v:7,d}")


if __name__ == "__main__":
    main()
