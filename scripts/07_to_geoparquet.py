#!/usr/bin/env python3
"""ジオコーディング済みCSVから GeoParquet を出力する。

座標が付与できた行のみを点フィーチャとして書き出す（座標なしの6,325行は
ジオメトリを持てないため対象外。全行が必要な場合は CSV を参照）。

型について:
  原本の列は原則そのまま文字列で保持する。水質項目には『不検出』『微量』
  『痕跡』『Ca併記』のような定性値が最大24.3%（NH4-N）含まれており、
  数値化すると情報が落ちるため。
  非空セルが100%数値である列（NUMERIC_SAFE）のみ float64 に変換する。
  これは情報損失なしに解析で使えるようにするための措置。
"""
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "output" / "f9_groundwater_all_geocoded.csv"
OUT = ROOT / "output" / "f9_groundwater_wells.parquet"

# 非空セルが100%数値であることを確認済みの列（情報損失なく float 化できる）
NUMERIC_SAFE = ["HEIGHT", "DEP", "SC", "SCL", "Q_01", "QSP_01", "TEMP", "PH"]


def main() -> None:
    df = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    total = len(df)

    g = df[(df.GC_LAT != "") & (df.GC_LON != "")].copy()
    lon = g.GC_LON.astype(float)
    lat = g.GC_LAT.astype(float)

    # 付与列は素直に型を付ける
    g["GC_LAT"] = lat
    g["GC_LON"] = lon
    g["GC_N_TOWNS"] = pd.to_numeric(g.GC_N_TOWNS, errors="coerce").astype("Int32")

    for c in NUMERIC_SAFE:
        conv = pd.to_numeric(g[c].replace("", None), errors="coerce")
        # 想定外の非数値が混ざっていたら黙って落とさず気付けるようにする
        lost = ((g[c] != "") & conv.isna()).sum()
        if lost:
            raise ValueError(f"{c}: 数値化で {lost} 件が欠損する。NUMERIC_SAFE から外すこと")
        g[c] = conv

    gdf = gpd.GeoDataFrame(
        g.drop(columns=["X", "Y"]),  # 原本の緯度経度列は全行空なので落とす
        geometry=[Point(x, y) for x, y in zip(lon, lat)],
        crs="EPSG:4326",
    )
    # covering（bbox列）は GeoParquet 1.1 の機能なので schema_version も揃えておく。
    # 空間フィルタを効かせられるため DuckDB spatial などから範囲読みが速くなる。
    gdf.to_parquet(OUT, index=False, compression="zstd", geometry_encoding="WKB",
                   schema_version="1.1.0", write_covering_bbox=True)

    size = OUT.stat().st_size
    print(f"出力: {OUT}")
    print(f"  {len(gdf):,} フィーチャ × {len(gdf.columns)} 列  ({size/1024/1024:.1f} MB)")
    print(f"  （台帳 {total:,} 行のうち座標なし {total - len(gdf):,} 行は対象外）")
    print(f"  CRS: {gdf.crs}")
    print(f"  bbox: {[round(v, 4) for v in gdf.total_bounds]}")
    print("\n精度レベル別:")
    for k, v in gdf.GC_LEVEL.value_counts().items():
        print(f"  {k:6s} {v:7,d}")
    print("\nfloat 化した列:", ", ".join(NUMERIC_SAFE))


if __name__ == "__main__":
    main()
