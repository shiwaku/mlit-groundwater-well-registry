#!/usr/bin/env python3
"""位置図PDFから復元した実座標と、ジオコーディング結果の誤差を実測する。

`09_pdf_truth.py` が作る平成24年度の563点が唯一の正解データである。
台帳の行との対応づけは次の性質を使う。

  位置図のラベルにある整理番号は、県内・カテゴリ内（政令市／市／町村）の
  出現順に振られており、同じ市町村コードの中では原本の行順と一致する。
  したがって「市町村コードでまとめ、整理番号順と行順を突き合わせる」だけで
  1対1に決まる。使用目的コードが一致することで検算できる。

使い方:
  python3 scripts/10_eval_accuracy.py
  python3 scripts/10_eval_accuracy.py --csv   # 突合結果を output/ に書き出す
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
GEO = ROOT / "output" / "f9_groundwater_all_geocoded.csv"
TRUTH = ROOT / "output" / "f9_pdf_truth_2012.csv"
OUT = ROOT / "output" / "f9_geocode_error_2012.csv"

TRUTH_YEAR = "2012"


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="store_true", help="突合結果をCSVに書き出す")
    args = ap.parse_args()

    geo = pd.read_csv(GEO, dtype=str, keep_default_na=False)
    truth = pd.read_csv(TRUTH, dtype={"PREF": str, "MUNI_CD": str})
    geo["_row"] = np.arange(len(geo))
    tgt = geo[geo.NEN == TRUTH_YEAR]

    matched, skipped = [], []
    for code, tg in truth.groupby("MUNI_CD"):
        ours = tgt[tgt.MUNI_CD == code].sort_values("_row")
        if len(ours) != len(tg):
            skipped.append((code, len(tg), len(ours)))
            continue
        for (_, t), (_, o) in zip(tg.sort_values("SEQ").iterrows(), ours.iterrows()):
            km = (np.nan if not o.GC_LAT else
                  haversine_km(float(o.GC_LAT), float(o.GC_LON), t.TRUTH_LAT, t.TRUTH_LON))
            matched.append(dict(
                PREF=t.PREF, MUNI_CD=code, MUNI_NAME=t.MUNI_NAME, SEQ=t.SEQ,
                ADR=o.ADR, USE_PDF=t.USE, USE_SRC=o.USE,
                GC_LEVEL=o.GC_LEVEL or "none", GC_METHOD=o.GC_METHOD,
                GC_LAT=o.GC_LAT, GC_LON=o.GC_LON,
                TRUTH_LAT=t.TRUTH_LAT, TRUTH_LON=t.TRUTH_LON,
                IN_MUNI=t.IN_MUNI, ERROR_KM=None if np.isnan(km) else round(km, 3)))
    m = pd.DataFrame(matched)

    print(f"正解データ {len(truth)}点 / 突合できた {len(m)}点")
    if skipped:
        print("  件数が合わず除外:", ", ".join(f"{c}(PDF{a}/台帳{b})" for c, a, b in skipped))
    agree = (m.USE_PDF.astype(str) == m.USE_SRC.astype(str)).mean()
    print(f"  使用目的コードの一致率: {agree:.1%}（対応づけの検算）")

    ok = m[m.ERROR_KM.notna()]
    out_muni = ok[~ok.IN_MUNI.astype(bool)]
    if len(out_muni):
        print(f"\n※ 正解点がラベルの市町村から外れている行が {len(out_muni)}件ある。"
              "原本の市町村コードが誤っている行（熊本の益城町/美里町など）で、"
              "ジオコーディングは誤ったコードの重心を返すため誤差に含めている。")
    print(f"\n■ GC_LEVEL 別の実測誤差（km） n={len(ok)}")
    hdr = f"{'level':<6}{'n':>5}{'中央値':>8}{'平均':>8}{'p90':>8}{'最大':>8}"
    print(hdr)
    for lv, g in ok.groupby("GC_LEVEL"):
        e = g.ERROR_KM
        print(f"{lv:<6}{len(g):>5}{e.median():>8.2f}{e.mean():>8.2f}"
              f"{e.quantile(.9):>8.2f}{e.max():>8.2f}")
    e = ok.ERROR_KM
    print(f"{'全体':<6}{len(ok):>5}{e.median():>8.2f}{e.mean():>8.2f}"
          f"{e.quantile(.9):>8.2f}{e.max():>8.2f}")

    print(f"\n■ GC_METHOD 別")
    for mt, g in ok.groupby("GC_METHOD"):
        print(f"{mt:<18}{len(g):>5}  中央値 {g.ERROR_KM.median():>6.2f}  最大 {g.ERROR_KM.max():>6.2f}")

    print(f"\n■ 誤差の大きい行 上位10")
    for _, r in ok.nlargest(10, "ERROR_KM").iterrows():
        print(f"  {r.ERROR_KM:6.2f}km {r.MUNI_CD} {r.MUNI_NAME:<8} {r.GC_LEVEL:<5} {r.ADR}")

    print(f"\n■ 参考: しきい値以内に入る割合")
    for t in (0.2, 0.5, 1.0, 2.0, 5.0):
        print(f"  {t:>4}km 以内: {(e <= t).mean():6.1%}")

    if args.csv:
        m.to_csv(OUT, index=False)
        print(f"\n-> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
