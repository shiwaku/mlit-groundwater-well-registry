#!/usr/bin/env python3
"""座標付きの2003年版（地域別シェープファイル）を WARP から取得する。

現行の配布物（scripts/00_download.py が取る都道府県別 zip）は X/Y 列が全行空だが、
2011年頃まで併せて配布されていた**地域別 zip 8本**の中身はシェープファイルで、
点フィーチャとして座標を持っている。合計 57,847 点。

配布元の tochi.mlit.go.jp は既に消えており、Wayback Machine にも地域別 zip は
残っていない。国立国会図書館の WARP（インターネット資料収集保存事業）が
2009-02-17 に収集したものだけが現存する。

  一覧ページ: http://tochi.mlit.go.jp/tockok/tochimizu/F9/download.html
  WARP 側は pywb で、`{コレクションID}/{タイムスタンプ}id_/{原URL}` が生データを返す
  （`id_` を付けないと閲覧用の HTML ラッパーが返る）。

なお `S_TKY2JGD` 列があるとおり、座標は日本測地系から JGD2000 に変換済みである。
逆変換すると秒がほぼ整数に揃うため、原本の粒度は日本測地系の秒単位（約30m）。
"""
import time
import urllib.request
from pathlib import Path

# WARP の 2009-02-17 収集分。コレクションID 20090319 は収集セットの識別子。
BASE = ("https://warp.ndl.go.jp/20090319/20090217012940id_"
        "/http://tochi.mlit.go.jp/tockok/tochimizu/F9/data")
REGIONS = ["hokkaido", "touhoku", "kantou", "hokuriku",
           "chuubu", "kinki", "c_shikoku", "kyuushuu"]

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "data" / "raw" / "shp2003"
UA = {"User-Agent": "mlit-groundwater-well-registry/1.0 (+research use)"}


def fetch(url: str, dest: Path, retries: int = 3) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  skip (既存): {dest.name}")
        return True
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=180) as r:
                body = r.read()
            # ラッパー HTML が返っていないか確かめる（zip は PK で始まる）
            if not body.startswith(b"PK"):
                raise ValueError(f"zip ではない応答 ({len(body):,} bytes)")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            print(f"  {dest.name}: {len(body):,} bytes")
            return True
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                print(f"  失敗: {url} ({e})")
                return False
            time.sleep(2 * (i + 1))
    return False


def main() -> None:
    ok = ng = 0
    for name in REGIONS:
        if fetch(f"{BASE}/{name}.zip", DEST / f"{name}.zip"):
            ok += 1
        else:
            ng += 1
    print(f"\n完了: 成功 {ok} / 失敗 {ng}  → {DEST}")


if __name__ == "__main__":
    main()
