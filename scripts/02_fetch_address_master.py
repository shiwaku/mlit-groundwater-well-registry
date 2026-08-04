#!/usr/bin/env python3
"""Geolonia japanese-addresses-v2 から市区町村マスタと全国町字インデックスを取得する。

出力:
  cache/ja.json                 … 都道府県・市区町村一覧（代表点つき）
  cache/towns/<pref>/<city>.json … 市区町村ごとの町字一覧（生データ）
  master_city.csv               … 5桁コード -> 都道府県名/市区町村名/区名/代表点
  master_town.csv               … 都道府県名/市区町村名/町字名/丁目/座標（全国）
"""
import csv
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://japanese-addresses-v2.geoloniamaps.com/api/ja"
ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "data" / "address_master"
CACHE = MASTER / "cache"
TOWNS = CACHE / "towns"
UA = {"User-Agent": "kokjo-f9-geocode/1.0 (+local research use)"}


def get_json(url: str, dest: Path, retries: int = 4):
    """取得してキャッシュ。既にキャッシュがあればそれを返す。"""
    if dest.exists() and dest.stat().st_size > 0:
        return json.loads(dest.read_text(encoding="utf-8"))
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            return json.loads(body.decode("utf-8"))
        except Exception as e:  # noqa: BLE001  ネットワーク起因は素直にリトライ
            last = e
            time.sleep(1.5 * (i + 1))
    print(f"  取得失敗: {url} ({last})")
    return None


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    ja = get_json(f"{API}.json", CACHE / "ja.json")
    assert ja, "ja.json が取得できませんでした"

    cities = []
    for pref in ja["data"]:
        pname = pref["pref"]
        for c in pref["cities"]:
            code5 = f"{int(c['code']):06d}"[:5]
            cities.append(
                {
                    "MUNI_CD": code5,
                    "pref": pname,
                    "county": c.get("county") or "",
                    "city": c["city"],
                    "ward": c.get("ward") or "",
                    "lon": (c.get("point") or [None, None])[0],
                    "lat": (c.get("point") or [None, None])[1],
                }
            )
    with (MASTER / "master_city.csv").open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(
            fp, fieldnames=["MUNI_CD", "pref", "county", "city", "ward", "lon", "lat"]
        )
        w.writeheader()
        w.writerows(cities)
    print(f"master_city.csv: {len(cities):,} 市区町村")

    # 町字一覧を市区町村ごとに取得。
    # v2 の URL は 郡名+市区町村名+区名 を1セグメントに連結した形（例 雨竜郡妹背牛町）。
    def fetch(c):
        seg = c["county"] + c["city"] + c["ward"]
        url = f"{API}/{urllib.parse.quote(c['pref'])}/{urllib.parse.quote(seg)}.json"
        dest = TOWNS / c["pref"] / f"{seg}.json"
        return c, get_json(url, dest)

    rows = []
    ok = ng = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, (c, data) in enumerate(ex.map(fetch, cities), 1):
            if not data:
                ng += 1
                continue
            ok += 1
            for t in data.get("data", []):
                pt = t.get("point") or [None, None]
                rows.append(
                    {
                        "MUNI_CD": c["MUNI_CD"],
                        "pref": c["pref"],
                        "county": c["county"],
                        "city": c["city"],
                        "ward": c["ward"],
                        "machiaza_id": t.get("machiaza_id", ""),
                        "oaza_cho": t.get("oaza_cho", "") or "",
                        "chome": t.get("chome", "") or "",
                        "koaza": t.get("koaza", "") or "",
                        "lon": pt[0],
                        "lat": pt[1],
                    }
                )
            if i % 200 == 0:
                print(f"  {i}/{len(cities)} 市区町村 … 町字 {len(rows):,} 件")

    with (MASTER / "master_town.csv").open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(
            fp,
            fieldnames=["MUNI_CD", "pref", "county", "city", "ward", "machiaza_id",
                        "oaza_cho", "chome", "koaza", "lon", "lat"],
        )
        w.writeheader()
        w.writerows(rows)
    print(f"master_town.csv: {len(rows):,} 町字 (取得成功 {ok} / 失敗 {ng} 市区町村)")


if __name__ == "__main__":
    main()
