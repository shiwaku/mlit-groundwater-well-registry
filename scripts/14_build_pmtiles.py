#!/usr/bin/env python3
"""2003年版の井戸57,847点を PMTiles（ベクタタイル）に変換する。ビューワ用。

  output/f9_wells_2003.parquet  →  output/f9_wells_2003.pmtiles

全点を入れる。位置精度の悪い `fallback`（16,081点・市区町村代表点）も落とさず、
ビューワ側で `POS_QUALITY` による色分けとフィルタに使う。落とすと「全部が実位置」に
見えてしまい、かえって誤解を招くため。

タイルに載せる属性:
  原本の列はそのまま載せる（ポップアップで全項目を見せるため）。
  加えて、ベクタタイルの式では文字列→数値の変換が使えない／重いので、
  フィルタと段彩に使う列だけ数値化した派生列を足す。

    ADR_DIST_KM_N  ADR_DIST_KM を float 化。判定不能（空文字）は -1
    NEN_N          NEN を int 化。不明（0・空）は 0
    PREF_NAME      PREF コードから引いた都道府県名（ポップアップと検索用）

  空文字のセルは属性ごと落とす（タイルサイズを小さくするため）。
  ビューワ側は「キーが無い＝空」として扱う。

ズームについて（実測して決めた）:
  z4-z9 を作り、z9 より上は overzoom で拡大する。点なので破綻しない。
  z9 のタイル内座標の刻みは 78km/4096 ≒ 19m で、**原本の座標の粒度（日本測地系の
  秒単位＝緯度約31m・経度約25m）より細かい**ので、これ以上の maxzoom は情報を増やさない。

  `-r1` で tippecanoe 既定のズーム別間引きを止めたうえで、タイルが
  `--maximum-tile-bytes` を超えたときだけ密なところから落とす（--drop-densest-as-needed）。
  結果として **最大ズーム z9 には全57,847点がそのまま入る**。z8 以下は入りきらないぶんが落ちる
  （tippecanoe 2.79.0 での実測）:

    z4 7,796 / z5 7,728 / z6 11,589 / z7 19,587 / z8 44,604 / z9 全点

  どこまで入るかは tippecanoe の版で変わる（2.80.0 では z8 でも全点入った）。
  下の completeFromZoom は保守的に maxzoom を書いている。

  低ズームで欠けるのは表示の都合であってデータの欠落ではない。ビューワの件数表示に
  描画中のフィーチャ数を使うと縮尺で数が変わって誤解を招くため、件数は
  このスクリプトが出す metadata の集計値を使うこと。

  なお全ズームに全点を入れる（-r1 のみ・サイズ制限なし）と 48MB になり、
  かつ日本全国表示で1タイル5MB を引くことになるので採らなかった。
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "output" / "f9_wells_2003.parquet"
OUT = ROOT / "output" / "f9_wells_2003.pmtiles"
LAYER = "wells"

MINZOOM, MAXZOOM = 4, 9
# タイル1枚の上限。超えたぶんだけ密なところから落とす。
MAX_TILE_BYTES = 600_000

PREF_NAMES = [
    "", "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]

# ジオメトリ列は WKB なので載せない。
# LAT / LON はジオメトリと重複するが、あえて属性としても載せる。タイルの座標は
# z9 で約19mに量子化されるため、ポップアップの表示値と Google マップ／ストリートビューの
# リンク（現地確認に使う）には原本の値をそのまま使いたいから。
DROP = ["geometry", "bbox"]


def to_geojsonseq(df: pd.DataFrame, path: Path) -> int:
    """1行1フィーチャの GeoJSONSeq（改行区切り）を書く。tippecanoe の入力形式。"""
    lon = df.LON.astype(float)
    lat = df.LAT.astype(float)
    props = df.drop(columns=[c for c in DROP if c in df.columns])
    records = props.to_dict("records")

    n = 0
    with path.open("w", encoding="utf-8") as f:
        for i, rec in enumerate(records):
            # 空セルは属性ごと落とす。NaN も同様（JSON の null を載せても意味がない）
            p = {k: v for k, v in rec.items() if v is not None and v == v and v != ""}
            f.write(json.dumps({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon.iat[i], 6),
                                                              round(lat.iat[i], 6)]},
                "properties": p,
            }, ensure_ascii=False) + "\n")
            n += 1
    return n


def main() -> None:
    if shutil.which("tippecanoe") is None:
        sys.exit("tippecanoe が見つからない。https://github.com/felt/tippecanoe を入れること")

    df = pd.read_parquet(SRC)

    # 緯度経度は6桁（約0.1m）で足りる。float の刻み（35.68656794111066）をそのまま
    # 載せると読みにくいうえタイルも太る。原本の粒度は秒単位なので情報は落ちない。
    df["LAT"] = df.LAT.round(6)
    df["LON"] = df.LON.round(6)

    # --- 派生列（ベクタタイルの式から直接使えるように数値で持たせる） ---
    dist = pd.to_numeric(df.ADR_DIST_KM, errors="coerce")
    df["ADR_DIST_KM_N"] = dist.fillna(-1).round(3)
    df["NEN_N"] = pd.to_numeric(df.NEN, errors="coerce").fillna(0).astype(int)
    df["PREF_NAME"] = pd.to_numeric(df.PREF, errors="coerce").fillna(0).astype(int).map(
        lambda i: PREF_NAMES[i] if 0 < i < len(PREF_NAMES) else ""
    )

    with tempfile.TemporaryDirectory() as tmp:
        seq = Path(tmp) / "wells.geojsonseq"
        n = to_geojsonseq(df, seq)
        print(f"GeoJSONSeq: {n:,} フィーチャ ({seq.stat().st_size/1024/1024:.1f} MB)")

        OUT.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "tippecanoe",
            "-o", str(OUT), "--force",
            "-l", LAYER,
            "-Z", str(MINZOOM), "-z", str(MAXZOOM),
            # -r1: ズーム別のレート間引きを止める（既定だと z8 でも半分以下に減る）
            "-r1", "--no-feature-limit",
            # そのうえで、タイルが上限を超えたときだけ密なところから落とす
            "--drop-densest-as-needed", "--maximum-tile-bytes", str(MAX_TILE_BYTES),
            "--preserve-input-order",
            str(seq),
        ]
        print("$", " ".join(cmd))
        subprocess.run(cmd, check=True)

    size = OUT.stat().st_size
    print(f"\n出力: {OUT}  ({size/1024/1024:.1f} MB)")
    print(f"  source-layer: {LAYER} / zoom {MINZOOM}-{MAXZOOM} / {n:,} 点")
    print("\n位置精度別:")
    for k, v in df.POS_QUALITY.value_counts().items():
        print(f"  {k:9s} {v:7,d}  ({v/len(df)*100:4.1f}%)")

    write_stats(df)


def write_stats(df: pd.DataFrame) -> None:
    """ビューワが「全体の何件か」を出すための集計。

    低ズームでは点が間引かれるので、描画中のフィーチャ数を数えると縮尺で数が変わる。
    母数は必ずこちらを使う。
    """
    dist = pd.to_numeric(df.ADR_DIST_KM, errors="coerce")
    stats = {
        "total": int(len(df)),
        "posQuality": {k: int(v) for k, v in df.POS_QUALITY.value_counts().items()},
        "use": {str(k): int(v) for k, v in df.USE.value_counts().items()},
        "distResolved": int(dist.notna().sum()),
        "nenRange": [int(pd.to_numeric(df.NEN, errors="coerce").replace(0, pd.NA).min()),
                     int(pd.to_numeric(df.NEN, errors="coerce").max())],
        "minzoom": MINZOOM,
        "maxzoom": MAXZOOM,
        "completeFromZoom": MAXZOOM,
    }
    out = ROOT / "data" / "viewer_stats.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n集計: {out}")


if __name__ == "__main__":
    main()
