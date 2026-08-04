#!/usr/bin/env python3
"""台帳の ADR（井戸の所在地）を正規化し、ジオコーディング用の候補文字列を段階生成する。

ADR は「都市部は町名、その他は大字程度まで」の粒度で、施設名・方角注記・番地が
混ざっている。1回の正規化で当てにいくのではなく、保守的な正規化から順に
アグレッシブに削っていく候補列を作り、当たった段階で採用する方針をとる。

candidates("円山動物園敷地内") -> ['円山動物園敷地内', '円山動物園', ...]
"""
import re
import unicodedata

# 括弧内の注記（施設名・補足）。全角/半角どちらも。
_PAREN = re.compile(r"[（(][^（()）]*[)）]")
# 閉じ括弧が無いまま文末まで続くもの（原本に103行ある文字切れ）
_PAREN_OPEN = re.compile(r"[（(].*$")
# 位置・方角の注記。語尾のみを対象にして正当な町名（真駒内・川内など）を壊さない。
_LOC_SUFFIX = re.compile(
    r"(?:敷地内|団地内|工業団地内|構内|地内|地先|付近|近傍|隣接地|"
    r"の[東西南北](?:側|方)?|[東西南北]側|地区内)$"
)
# 番地・号・線などの数値表記
_BANCHI = re.compile(r"(?:[0-9]+(?:-[0-9]+)*(?:番地|番|号)?)$")
# 施設名の語尾
_FACILITY = re.compile(
    r"(?:小学校|中学校|高等学校|高校|学校|幼稚園|保育園|大学|"
    r"工場|製作所|事業所|営業所|発電所|浄水場|下水処理場|清掃工場|処理場|"
    r"団地|工業団地|公園|動物園|球場|競技場|"
    r"役場|役所|支所|出張所|公民館|病院|医院|センター|会館|"
    r"駅|操車場|車庫|寮|荘|邸|神社|寺|城跡)$"
)


def base_normalize(adr: str) -> str:
    """全角→半角、括弧内注記の除去、空白の整理までを行う保守的な正規化。"""
    if not adr:
        return ""
    s = unicodedata.normalize("NFKC", adr)
    # 括弧内の注記を除去（入れ子は想定しない）。閉じ括弧欠落は文末まで落とす。
    prev = None
    while prev != s:
        prev = s
        s = _PAREN.sub("", s)
    s = _PAREN_OPEN.sub("", s)
    # 開き括弧を伴わない孤立した閉じ括弧（'立神津小学校の西）' のような原本の崩れ）
    s = re.sub(r"[)）]", "", s)
    # 読点・中黒・全角記号の整理
    s = s.replace("、", " ").replace("・", "").replace("〜", "-").replace("～", "-")
    s = re.sub(r"[\s　]+", " ", s).strip()
    return s


def _strip_repeat(s: str, pat: re.Pattern, limit: int = 3) -> str:
    """語尾パターンを繰り返し剥がす（例 『団地内』→『団地』の二段になる場合がある）。"""
    for _ in range(limit):
        new = pat.sub("", s).strip()
        if new == s or not new:
            return s if not new else new
        s = new
    return s


def candidates(adr: str) -> list[str]:
    """ADR から試行順に並べた候補文字列を返す（重複・空文字は除去）。"""
    out: list[str] = []

    def add(v: str) -> None:
        v = v.strip(" -　")
        if v and v not in out:
            out.append(v)

    s = base_normalize(adr)
    if not s:
        return []
    add(s)

    # 空白で区切られた後半は施設名・補足が多い（例 '宮前町 国鉄旭川工場内'）
    if " " in s:
        add(s.split(" ")[0])

    # 位置・方角注記を剥がす
    s2 = _strip_repeat(s, _LOC_SUFFIX)
    add(s2)

    # 番地・号を落とす
    s3 = _BANCHI.sub("", s2).strip()
    add(s3)
    if " " in s3:
        add(s3.split(" ")[0])

    # 施設名の語尾を落とす（ここまで来て当たらない場合の最後の手段）
    s4 = _strip_repeat(s3, _FACILITY)
    add(s4)
    s5 = _BANCHI.sub("", s4).strip()
    add(s5)

    # 「大字」「字」の有無ゆれ両方を候補に入れる
    for v in list(out):
        if "大字" in v:
            add(v.replace("大字", ""))
        if re.search(r"(?<!大)字", v):
            # 『大字』の字は残す（大字ごと消す候補は上で別に作っている）
            add(re.sub(r"(?<!大)字", "", v))
        # 丁目まで残っていれば丁目より前だけも試す
        m = re.match(r"^(.*?[0-9]+)丁目", v)
        if m:
            add(m.group(0))
            add(re.sub(r"[0-9]+丁目.*$", "", v))

    return [v for v in out if v]


if __name__ == "__main__":
    tests = [
        "円山動物園敷地内", "茨戸町茨戸公園団地内", "宮前町 国鉄旭川工場内",
        "中央区南５条東２丁目", "北12条東8丁目(北光小学校)", "吹張町（",
        "字福永　　（道北食肉施設№", "宝生地先", "真駒内", "川内",
        "水門町６－２７の北側隣接地", "厚別町下野幌3-17番地(第2清掃工場)",
        "大戸町大字小谷字戸倉", "桟橋通り4-10-1", "立神津小学校の西）",
    ]
    for t in tests:
        print(f"{t!r}\n    -> {candidates(t)}")
