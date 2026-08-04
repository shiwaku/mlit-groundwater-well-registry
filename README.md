# mlit-groundwater-well-registry

国土交通省「[土地分類調査・水基本調査 全国地下水資料台帳調査](https://nlftp.mlit.go.jp/kokjo/inspect/landclassification/water/f9_exp.html)」（F9）の
都道府県別データを **1本のCSVにマージし、住所からジオコーディングして緯度経度を付与** したものです。

> [!IMPORTANT]
> **本リポジトリは非公式です。** 国土交通省が作成・配布しているものではありません。
> 元データの公式な配布元は上記の国土数値情報ダウンロードサイトです。
> 加工の過程で誤りが入り込んでいる可能性があるため、正確性が要求される用途では必ず原本を参照してください。

## このデータについて

昭和27年度以降に累積された **深井戸（概ね30m以深）71,198件** の記録です。掘削時の地質、揚水試験による帯水層情報、水質検査結果を含みます。

| ファイル | 内容 | 行 × 列 |
|---|---|---|
| `output/f9_groundwater_all.csv` | 47都道府県をマージしたもの（原本の項目のみ） | 71,198 × 48 |
| `output/f9_groundwater_all_geocoded.csv` | 上記に緯度経度と精度情報を付与したもの | 71,198 × 57 |

文字コードは **UTF-8（BOMなし）**、改行は CRLF です。

## なぜジオコーディングが必要か

**原本には緯度経度が入っていません。** 項目定義（`doc/koumoku.xls`）に「4. 緯度・経度：非表示」と明記されており、
`X`/`Y` 列は全71,198行が空でした。位置情報は `ADR`（井戸の所在地）列のみで、しかも
**都道府県名も市区町村名も含まれない町名・大字だけ**（平均4.3文字）です。

さらに `MUNI_CD`（`PREF`+`CITY`）の **17.7%（12,568行 / 1,284コード）が現行の市区町村コード表に存在しません**。
調査年度が1952〜2024年にわたるため、平成の大合併前のコードが混在しています。

そのため「コードから自治体名を復元 → 住所文字列を合成 → 正規化 → 座標付与」という工程が必要になります。

## ジオコーディング結果

| 精度レベル | 行数 | 割合 | 内容 |
|---|---:|---:|---|
| `town` 町丁目レベル | 44,132 | 62.0% | 町字の代表点 |
| `oaza` 大字・地区レベル | 5,678 | 8.0% | 該当する複数町字の重心 |
| `city` 市区町村レベル | 15,063 | 21.2% | 市役所などの代表点 |
| `none` 座標なし | 6,325 | 8.9% | 自治体を特定できず |
| **座標付与できた行** | **64,873** | **91.1%** | |

信頼度（`GC_CONFIDENCE`）別では high 53.4% / medium 14.0% / low 23.7% です。

**精度の上限について。** 項目定義に「都市部は町名、その他は大字程度まで」とある通り、原本の住所粒度は
町丁目・大字レベルです。**番地レベルの精度は原理的に出ません**（数百mオーダーが上限）。
個々の井戸のピンポイントな位置としては使えない前提でご利用ください。

## 付与した列

| 列 | 内容 |
|---|---|
| `MUNI_CD` | `PREF`+`CITY` から組み立てた5桁の市区町村コード（原本のコード。合併前のものを含む） |
| `GC_LAT` / `GC_LON` | 緯度経度（EPSG:4326） |
| `GC_LEVEL` | `town` / `oaza` / `city` / `none` |
| `GC_METHOD` | どの経路で決まったか（下表） |
| `GC_CONFIDENCE` | `high` / `medium` / `low` |
| `GC_ADDR_USED` | 実際に照合に使った住所文字列 |
| `GC_MUNI_CD_RESOLVED` | 名寄せ後の**現行**市区町村コード |
| `GC_MUNI_RESOLVED` | 名寄せ後の市区町村名 |
| `GC_N_TOWNS` | 重心を取った町字の件数（`GC_LEVEL=oaza` のとき） |

### GC_METHOD の内訳

| 経路 | 行数 | 信頼度 | 説明 |
|---|---:|---|---|
| `A_normalize` | 38,038 | high | 現行コードから自治体名を引き、正規化エンジンが町字を確定 |
| `A_oaza_exact` | 4,350 | medium | 大字名が完全一致。該当町字群の重心 |
| `A_oaza_prefix` | 1,165 | medium | 大字名が前方一致（例『篠路』→篠路◯条◯丁目87件の重心） |
| `B_town_exact` | 4,421 | medium | 合併前コード。県内で一意な大字名から自治体を推定 |
| `A_city_fallback` | 15,063 | low | 町字に届かず市区町村代表点 |
| `B_town_suffix` | 1,836 | low | 合併前コード。『旧自治体名＋大字名』の後方一致で推定 |
| `B_unmatched` | 3,938 | — | 県内に一致する大字名がなく特定不能 |
| `B_town_ambiguous` | 1,299 | — | 県内の複数自治体に該当し特定不能 |
| `B_no_adr` | 1,088 | — | `ADR` が空 |

> [!WARNING]
> **`B_*`（合併前コード）の名寄せは変遷テーブルによる確定ではなく、地名の一致による推定です。**
> 官報ベースで機械可読な変遷情報は2005年4月以降しか公開されておらず、平成の大合併のピーク（2004〜2005年3月）を
> カバーできないため、この方式を採りました。
> `01305`→石狩市（旧厚田村）、`01542`→大空町（旧女満別町）のように史実と一致する結果が大半ですが、
> 検算では一定の誤マッチが残ることを確認しています。**厳密さが必要な用途では `GC_CONFIDENCE=high` に絞ってください。**

## 原本の破損・表記ゆれへの対応

| 事象 | 件数 | 対応 |
|---|---:|---|
| `GEOLOGY` 欄にTABが混入し48列になる行（愛知） | 2 | 32列目末尾が `\|` で終わるため32+33列を連結して原形復元 |
| セルが全角スペースのみ | 多数 | 空文字に正規化 |
| `CITY` に5桁コード丸ごと（高知）／検査数字付き4桁（愛媛） | 7 | `CITY` 列は原本のまま保持し、`MUNI_CD` 生成時のみ正規化 |
| `CITY='13'`（静岡・横砂西町）＝判定不能 | 1 | `MUNI_CD` を空に |
| 政令市の行政区名が `ADR` にも入り連結すると二重化 | 920 | 区名マスタと照合して除去 |
| 閉じ括弧が欠落した施設名（`吹張町（` 等） | 103 | 開き括弧から文末までを注記として除去 |
| `NEN`（調査年度）が `0` | 200 | そのまま保持（有効な範囲は1952〜2024年度） |

日付欄の `00000000` は未測定を示すプレースホルダとして原本のまま残しています。
なお元データはページ説明では「パイプ区切り」とありますが、実際は **CP932 / TAB区切り / ヘッダ行あり** で、
`|` はフィールド内の多値区切り（`SCREENS`=`18-29|34.5-45.5`、`GEOLOGY`=`深度|化石|地質名称` の繰り返し）です。

## ディレクトリ構成

```
.
├── scripts/
│   ├── 00_download.py              原本を国交省サイトから取得
│   ├── 01_merge_to_csv.py          47都道府県の .dat を1本のCSVへ
│   ├── 02_fetch_address_master.py  Geolonia 住所マスタを取得
│   ├── 03_build_geocode_input.py   住所文字列の合成と候補生成（track A/B に振り分け）
│   ├── 04_geocode.mjs              正規化エンジンで座標付与（track A）
│   ├── 05_resolve_legacy_codes.py  合併前コードを町字名マッチで名寄せ（track B）
│   ├── 06_finalize.py              結果を統合し精度フラグ付きで最終CSV出力
│   └── lib/adr_norm.py             ADR（所在地）の正規化と候補生成
├── data/
│   ├── raw/{zip,doc}/              原本（gitignore・00 で再取得）
│   └── address_master/             住所マスタ（cache と町字マスタは gitignore）
├── work/                           中間生成物（gitignore）
└── output/                         成果物CSV
```

## 再現手順

```bash
pip install pandas openpyxl xlrd
npm install

python3 scripts/00_download.py              # 原本DL（zip 6.3MB + doc 13MB）
python3 scripts/01_merge_to_csv.py          # -> output/f9_groundwater_all.csv
python3 scripts/02_fetch_address_master.py  # 住所マスタ取得（約147MB・数分）
python3 scripts/03_build_geocode_input.py
node    scripts/04_geocode.mjs              # 約38,600件・数分（中断しても再開可）
python3 scripts/05_resolve_legacy_codes.py
python3 scripts/06_finalize.py              # -> output/f9_groundwater_all_geocoded.csv
```

`04_geocode.mjs` は結果を追記式に書き出すため、中断しても再実行すれば続きから処理します。

## 出典・ライセンス

元データは **公共データ利用規約第1.0版（PDL1.0）** で提供されており、出典表示のうえで加工・再配布が可能です。

- 「[国土数値情報（全国地下水資料台帳調査）](https://nlftp.mlit.go.jp/kokjo/inspect/landclassification/water/f9_exp.html)」（国土交通省）をもとに加工・作成
- 市区町村コードの照合に「[全国地方公共団体コード](https://www.soumu.go.jp/denshijiti/code.html)」（総務省）を使用
- ジオコーディングに [@geolonia/normalize-japanese-addresses](https://github.com/geolonia/normalize-japanese-addresses) v3.1.3（MIT）を使用。
  住所データ [@geolonia/japanese-addresses-v2](https://github.com/geolonia/japanese-addresses-v2) はデジタル庁「[アドレス・ベース・レジストリ](https://www.digital.go.jp/policies/base_registry_address)」を元に加工されたものです

住所データ API（`japanese-addresses-v2.geoloniamaps.com`）は現在無償公開されていますが、提供元により停止・変更される可能性があります。
継続的な利用では自前でのホスティングが推奨されています。

本リポジトリのスクリプトは MIT License です。出力CSVは元データの利用規約（PDL1.0）に従います。
