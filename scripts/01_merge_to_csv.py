#!/usr/bin/env python3
"""全国地下水資料台帳調査（F9）の都道府県別 .dat を1本のCSVにマージする。

入力: zip/01.zip ... zip/47.zip （各 NN.dat / CP932 / TAB区切り / ヘッダ行あり）
出力: output/f9_groundwater_all.csv （UTF-8 BOMなし, CRLF）

原本の47列をそのまま保持し、末尾に利便列 MUNI_CD（PREF+CITY の5桁市区町村コード）を追加する。
フィールド内の '|' は多値区切りとして原本のまま残す。
"""
import csv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZIP_DIR = ROOT / "data" / "raw" / "zip"
OUT = ROOT / "output" / "f9_groundwater_all.csv"

N_FIELDS = 47
# GEOLOGY 欄に誤ってTABが混入し48列になる行がある（愛知に2件）。
# 当該行は field[32] の末尾が '|' で終わるため、32と33を単純連結すれば原形に戻る。
GEOLOGY_IDX = 32


def clean(v: str) -> str:
    """前後の半角/全角空白を除去。全角スペースのみのセルは空にする。"""
    return v.strip().strip("　").strip()


def muni_cd(pref_raw: str, city_raw: str) -> str:
    """PREF+CITY から5桁の市区町村コードを組み立てる。

    CITY 欄には表記ゆれが9行ある（全国で）。解決可能なものだけ正規化し、
    判定できないものは空文字を返す（原本の PREF/CITY 列は無加工で保持している）。
      - 5桁で先頭2桁が都道府県コード … 市区町村コードが丸ごと入っている → 下3桁を採用
      - 4桁 … 3桁コード＋検査数字 → 上3桁を採用
    """
    pref, city = pref_raw.zfill(2), city_raw
    if not pref_raw or not city or not city.isdigit():
        return ""
    if len(city) == 5 and city[:2] == pref:
        city = city[2:]
    elif len(city) == 4:
        city = city[:3]
    if len(city) != 3:
        return ""
    return pref + city


def read_dat(pref: str) -> tuple[list[str], list[list[str]]]:
    with zipfile.ZipFile(ZIP_DIR / f"{pref}.zip") as z:
        text = z.read(f"{pref}.dat").decode("cp932")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = lines[0].split("\t")
    rows, repaired = [], 0
    for ln in lines[1:]:
        f = ln.split("\t")
        if len(f) == N_FIELDS + 1:
            f = f[:GEOLOGY_IDX] + [f[GEOLOGY_IDX] + f[GEOLOGY_IDX + 1]] + f[GEOLOGY_IDX + 2:]
            repaired += 1
        if len(f) != N_FIELDS:
            raise ValueError(f"{pref}.dat: 想定外の列数 {len(f)}: {ln[:120]!r}")
        rows.append([clean(v) for v in f])
    if repaired:
        print(f"  {pref}: GEOLOGY欄のTAB混入を{repaired}行修復")
    return header, rows


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = None
    total = 0
    unresolved: list[tuple[str, str, str, str]] = []
    with OUT.open("w", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        for i in range(1, 48):
            pref = f"{i:02d}"
            h, rows = read_dat(pref)
            if header is None:
                header = h
                w.writerow(header + ["MUNI_CD"])
            elif h != header:
                raise ValueError(f"{pref}.dat のヘッダが他と一致しません")
            for r in rows:
                muni = muni_cd(r[1], r[2])
                if not muni:
                    unresolved.append((pref, r[1], r[2], r[5]))
                w.writerow(r + [muni])
            total += len(rows)
            print(f"{pref}: {len(rows):6,d} 行")
    print(f"\n合計 {total:,} 行 -> {OUT}")
    if unresolved:
        print(f"\nMUNI_CD を組み立てられなかった行 {len(unresolved)} 件（PREF/CITY は原本のまま保持）:")
        for f, p, c, adr in unresolved:
            print(f"  {f}.dat  PREF={p!r} CITY={c!r} ADR={adr!r}")


if __name__ == "__main__":
    main()
