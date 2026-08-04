#!/usr/bin/env python3
"""全国地下水資料台帳調査（F9）の原本を国土交通省サイトから取得する。

取得先: https://nlftp.mlit.go.jp/kokjo/inspect/landclassification/water/f9_exp.html
  zip/01.zip ... zip/47.zip  都道府県別データ（中身は NN.dat）
  zip/template.zip           閲覧ツール（Excel VBA）
  doc/koumoku.xls            井戸台帳記載項目の説明
  doc/01.doc ... doc/15.doc  過年度調製の解説文（地域別）
"""
import time
import urllib.request
from pathlib import Path

BASE = "https://nlftp.mlit.go.jp/kokjo/tochimizu/F9"
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
UA = {"User-Agent": "mlit-groundwater-well-registry/1.0 (+research use)"}


def fetch(url: str, dest: Path, retries: int = 3) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  skip (既存): {dest.name}")
        return True
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
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
    targets = [(f"{BASE}/data/{i:02d}.zip", RAW / "zip" / f"{i:02d}.zip") for i in range(1, 48)]
    targets += [
        (f"{BASE}/data/template.zip", RAW / "zip" / "template.zip"),
        (f"{BASE}/data/koumoku.xls", RAW / "doc" / "koumoku.xls"),
    ]
    targets += [(f"{BASE}/text/{i:02d}.doc", RAW / "doc" / f"{i:02d}.doc") for i in range(1, 16)]
    for url, dest in targets:
        if fetch(url, dest):
            ok += 1
        else:
            ng += 1
    print(f"\n完了: 成功 {ok} / 失敗 {ng}")


if __name__ == "__main__":
    main()
