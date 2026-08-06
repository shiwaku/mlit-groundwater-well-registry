#!/usr/bin/env python3
"""2003年版の地域別シェープファイル8本を1つのデータセットにまとめる。

`11_download_shp2003.py` が取ってきた zip をそのまま読み、CSV と GeoParquet を出す。
座標付きの井戸 57,847 点。現行版（71,198行・座標なし）とは**結合しない**。
調査年次が違ううえ整理番号のような安定した主キーが無く、機械的な突合は
誤結合を生むため、突合は利用者の判断に委ねる。

シェープファイルの読み方について:
  zip 内のファイル名が cp932（`北海道.shp` など）で、fiona/pyogrio がそのまま
  開けないことがあるため、shp（点のみ・28バイト固定長）と dbf を自前で読む。

原本の欠陥を2つ引き継いでいる:

  1. DBF の `TEMP` 列（C254）には、水温が入っている行と**地質柱状図（本来の
     GEOLOGY 列）が入っている行が混在**する。254バイトを超えた分は `###...`
     になっている。判別できるよう `TEMP_KIND` 列を足した。
  2. `QSPD_02` `QSP_02` はフィールド長0で値を持たないため落とした。
     現行版の `GEOLOGY` に相当する独立した列も無い。

そして数値列は **空欄が 0 で埋められている**（PH の64.7%、FE の64.6%、CL の57.9%が 0）。
真の 0 と区別できないため、ここでは変換せず原本のまま出す。水質や揚水量を集計する用途には
現行版（f9_groundwater_all.csv・空欄を空欄のまま持つ）を使うこと。

座標について:
  `S_TKY2JGD` 列があるとおり日本測地系から JGD2000 へ変換済み。逆変換すると
  秒がほぼ整数に揃うため（残差の中央値 0.025 秒）、原本の粒度は
  日本測地系の秒単位＝緯度で約30m。EPSG:4326 として扱ってよい。
"""
import csv
import struct
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "raw" / "shp2003"
OUT_CSV = ROOT / "output" / "f9_wells_2003.csv"
OUT_PARQUET = ROOT / "output" / "f9_wells_2003.parquet"

# フィールド長0で値を持たない列
EMPTY_FIELDS = ["QSPD_02", "QSP_02"]

# 非空セルが実質100%数値の列のみ float 化する（07_to_geoparquet.py と同じ方針）。
# TEMP は水温と地質記述が混在するため対象外。
NUMERIC_SAFE = ["HEIGHT", "DEP", "SC", "SCL", "DIA", "PH",
                "NWL_01", "PWL_01", "Q_01", "QSP_01"]


def read_shapefile_zip(path: Path) -> pd.DataFrame:
    """点シェープファイルの zip から属性＋座標の DataFrame を作る。"""
    z = zipfile.ZipFile(path)
    members = {}
    for info in z.infolist():
        try:
            decoded = info.filename.encode("cp437").decode("cp932")
        except (UnicodeEncodeError, UnicodeDecodeError):
            decoded = info.filename
        members[decoded.rsplit(".", 1)[-1].lower()] = info.filename

    shp = z.read(members["shp"])
    shape_type = struct.unpack("<i", shp[32:36])[0]
    if shape_type != 1:
        raise ValueError(f"{path.name}: 点シェープファイルではない (type={shape_type})")
    n_points = (len(shp) - 100) // 28  # ヘッダ100 + レコード28バイト固定
    coords = [struct.unpack("<2d", shp[100 + i * 28 + 12:100 + i * 28 + 28])
              for i in range(n_points)]

    dbf = z.read(members["dbf"])
    n_rec, header_len, rec_len = struct.unpack("<IHH", dbf[4:12])
    if n_rec != n_points:
        raise ValueError(f"{path.name}: shp {n_points} 点 と dbf {n_rec} 件が合わない")

    fields = []
    for o in range(32, header_len - 1, 32):
        if dbf[o] == 0x0D:  # ヘッダ終端
            break
        name = dbf[o:o + 11].split(b"\x00")[0].decode("cp932", "replace")
        fields.append((name, dbf[o + 16]))

    rows = []
    for r in range(n_rec):
        rec = dbf[header_len + r * rec_len:header_len + (r + 1) * rec_len]
        pos = 1  # 先頭1バイトは削除フラグ
        row = {"LON": coords[r][0], "LAT": coords[r][1]}
        for name, length in fields:
            row[name] = rec[pos:pos + length].decode("cp932", "replace").strip()
            pos += length
        rows.append(row)
    return pd.DataFrame(rows)


def classify_temp(v: str) -> str:
    """TEMP 列の中身が水温か地質記述かを判別する。"""
    if not v:
        return ""
    if v.startswith("#"):
        return "overflow"   # 254バイト超で ### に潰れた地質記述
    if "|" in v:
        return "geology"    # 本来 GEOLOGY 列の値
    return "temp"


def main() -> None:
    zips = sorted(SRC.glob("*.zip"))
    if not zips:
        raise SystemExit(f"{SRC} に zip が無い。先に 11_download_shp2003.py を実行すること")

    parts = []
    for p in zips:
        df = read_shapefile_zip(p)
        df.insert(0, "REGION", p.stem)
        print(f"  {p.stem:10s} {len(df):6,d} 点")
        parts.append(df)
    df = pd.concat(parts, ignore_index=True)

    df = df.drop(columns=[c for c in EMPTY_FIELDS if c in df.columns])
    df["TEMP_KIND"] = df.TEMP.map(classify_temp)
    df["MUNI_CD"] = (df.PREF.astype(int).map("{:02d}".format)
                     + df.CITY.astype(int).map("{:03d}".format))

    front = ["REGION", "NEN", "PREF", "CITY", "MUNI_CD", "ADR", "LON", "LAT"]
    df = df[front + [c for c in df.columns if c not in front]]

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8", lineterminator="\r\n",
              quoting=csv.QUOTE_MINIMAL)

    g = df.copy()
    for c in NUMERIC_SAFE:
        conv = pd.to_numeric(g[c].replace("", None), errors="coerce")
        lost = ((g[c] != "") & conv.isna()).sum()
        # 原本の手入力ゆれ（'250～200' など）が数件ある。多すぎたら気付けるようにする
        if lost > len(g) * 0.001:
            raise ValueError(f"{c}: 数値化で {lost} 件が欠損する。NUMERIC_SAFE から外すこと")
        g[c] = conv
    gdf = gpd.GeoDataFrame(g, geometry=gpd.points_from_xy(g.LON, g.LAT), crs="EPSG:4326")
    gdf.to_parquet(OUT_PARQUET, index=False, compression="zstd", geometry_encoding="WKB",
                   schema_version="1.1.0", write_covering_bbox=True)

    print(f"\n出力: {OUT_CSV}  ({OUT_CSV.stat().st_size/1024/1024:.1f} MB)")
    print(f"出力: {OUT_PARQUET}  ({OUT_PARQUET.stat().st_size/1024/1024:.1f} MB)")
    print(f"  {len(df):,} 点 × {len(df.columns)} 列")
    print(f"  bbox: {[round(v, 4) for v in gdf.total_bounds]}")
    print(f"  調査年次 {df.NEN.min()}〜{df.NEN.max()} / {df.MUNI_CD.nunique():,} 市区町村")
    print("\nTEMP 列の中身:")
    for k, v in df.TEMP_KIND.value_counts().items():
        print(f"  {k or '(空)':9s} {v:7,d}")


if __name__ == "__main__":
    main()
