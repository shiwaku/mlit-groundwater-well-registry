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
  日本測地系から JGD2000 へ**変換済み**なので EPSG:4326 として扱ってよい。
  保存値の緯度の秒は整数に揃っていないが（0.05秒以内は12.0%＝ほぼランダム）、
  日本測地系へ逆変換すると揃う（71.9%）。原本が日本測地系の整数秒で記録されて
  いたことの裏返しで、粒度は緯度で約30m。船橋市の防災用井戸8点を番地まで
  ジオコーディングした実位置と突き合わせても、保存値のままなら平均ずれ131m
  （向きはばらばら）、日本測地系とみなして変換すると北483m・西334mの系統ズレが
  出る。二重変換しないこと。

位置の品質（`POS_QUALITY` 列）:
  **27.8%は実位置ではない。** 同一座標に複数行が重なり、しかも町名が異なる
  ものがある（大阪市中央区の1点に71種類の町名）。市区町村代表点への
  フォールバックで、精度は市区町村レベルしかない。判別できるよう分類した。

    unique   36,415行 (63.0%)  単独の座標。実位置とみなせる
    site      5,351行 ( 9.3%)  複数行が同一座標だが町名は同一。同一サイトの井戸群
    fallback 16,081行 (27.8%)  複数行が同一座標で町名が異なる。実位置ではない
"""
import csv
import re
import struct
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

PREF_NAMES = [
    "", "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]


def normalize_town(s: str) -> str:
    """『松が丘』『松ヶ丘』や『大字○○』の表記ゆれを吸収する。"""
    s = re.sub(r"[ヶケが]", "ケ", str(s))
    s = re.sub(r"^大字|^字", "", s)
    return re.sub(r"(町|村)$", "", s)


def town_of(adr: str) -> str:
    """ADR から丁目・字以下を落として町字名だけにする。"""
    s = re.sub(r"[0-9０-９一二三四五六七八九十]+丁目.*$", "", str(adr))
    s = re.sub(r"字.*$", "", s)
    return normalize_town(s)

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


def add_pos_quality(df: pd.DataFrame) -> pd.DataFrame:
    """同一座標の重なり方から位置の品質を分類する。

    町名が異なる行が同じ座標に載っていれば、それは井戸の位置ではなく
    市区町村代表点へのフォールバックである。町名が同一なら同一サイトの
    井戸群と解釈できるので区別する（ADR が空の場合は判断材料が無いため
    fallback 側に倒す）。
    """
    xy = df.LON.astype(str) + "," + df.LAT.astype(str)
    grp = df.assign(_xy=xy).groupby("_xy")
    n = grp.LON.transform("size")
    n_adr = grp.ADR.transform("nunique")
    has_adr = grp.ADR.transform(lambda s: (s != "").all())

    quality = pd.Series("fallback", index=df.index)
    quality[(n >= 2) & (n_adr <= 1) & has_adr] = "site"
    quality[n == 1] = "unique"
    return df.assign(POS_N=n.astype(int), POS_QUALITY=quality)


def add_adr_distance(df: pd.DataFrame) -> pd.DataFrame:
    """点が自分の `ADR`（町名）の位置に落ちているかを測る（`ADR_DIST_KM`）。

    `POS_QUALITY` は座標の重なり方だけを見るので、単独の座標でも値が誤っていれば
    `unique` になる。行ごとに疑わしさを見るための独立した指標として、住所マスタの
    町字代表点との距離を持たせる。

    同名の町字は全国にも県内にもあるため、県内で**一箇所に定まる地名**（同名町字群の
    広がりが2km以内）だけを使う。解決できるのは42%程度で、残りは空になる。

    住所マスタが無ければ黙って列を空にする（02_fetch_address_master.py で取得する）。
    """
    master = ROOT / "data" / "address_master" / "master_town.csv"
    if not master.exists():
        print(f"  住所マスタが無いため ADR_DIST_KM は空にする（{master}）")
        return df.assign(ADR_DIST_KM="")

    tm = pd.read_csv(master, dtype=str, keep_default_na=False)
    tm["lon"] = pd.to_numeric(tm.lon, errors="coerce")
    tm["lat"] = pd.to_numeric(tm.lat, errors="coerce")
    tm = tm[tm.lon.notna() & tm.lat.notna()]
    tm["key"] = tm.pref + "\t" + tm.oaza_cho.map(normalize_town)

    g = tm.groupby("key").agg(lon=("lon", "mean"), lat=("lat", "mean"),
                              lon_sd=("lon", "std"), lat_sd=("lat", "std"))
    g[["lon_sd", "lat_sd"]] = g[["lon_sd", "lat_sd"]].fillna(0.0)
    # 経度1度は北緯36度で約90km、緯度1度は約111km
    spread = np.hypot(g.lon_sd * 90.0, g.lat_sd * 111.1)
    g = g[spread <= 2.0]

    key = df.PREF.astype(int).map(lambda i: PREF_NAMES[i]) + "\t" + df.ADR.map(town_of)
    ref = key.map(g[["lon", "lat"]].to_dict("index"))
    t_lon = np.array([r["lon"] if isinstance(r, dict) else np.nan for r in ref])
    t_lat = np.array([r["lat"] if isinstance(r, dict) else np.nan for r in ref])

    lon = df.LON.to_numpy(float)
    lat = df.LAT.to_numpy(float)
    p1, p2 = np.radians(lat), np.radians(t_lat)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(t_lon - lon) / 2) ** 2)
    km = 2 * 6371.0088 * np.arcsin(np.sqrt(a))
    return df.assign(ADR_DIST_KM=pd.Series(km, index=df.index).round(3)
                     .map(lambda v: "" if pd.isna(v) else f"{v:.3f}"))


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
    df = add_pos_quality(df)
    df = add_adr_distance(df)

    front = ["REGION", "NEN", "PREF", "CITY", "MUNI_CD", "ADR", "LON", "LAT",
             "POS_QUALITY", "POS_N", "ADR_DIST_KM"]
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
    print("\n位置の品質:")
    ad = pd.to_numeric(df.ADR_DIST_KM, errors="coerce")
    print(f"  {'':9s} {'行数':>7s}  {'':6s}  ADR_DIST_KM（自分の町字からの距離）")
    for k in ("unique", "site", "fallback"):
        sel = df.POS_QUALITY == k
        s = ad[sel].dropna()
        stat = (f"n={len(s):6,d} 中央値 {s.median():5.2f}km  5km超 {(s > 5).mean()*100:4.1f}%"
                if len(s) else "（住所マスタ未取得）")
        print(f"  {k:9s} {sel.sum():7,d}  ({sel.sum()/len(df)*100:4.1f}%)  {stat}")
    print("\nTEMP 列の中身:")
    for k, v in df.TEMP_KIND.value_counts().items():
        print(f"  {k or '(空)':9s} {v:7,d}")


if __name__ == "__main__":
    main()
