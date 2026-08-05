"""公開用地下水調査地点位置図（平成24年度・PDF）から井戸座標を復元する。

このPDFは ArcMap 10.0 が出力したベクタPDFで、井戸は ESRIDefaultMarker
フォントのグリフ、ラベルは Arial の `整理番号 ,市町村コード ,使用目的` で
描かれている。GeoPDF ではないため座標系情報（/VP・/Measure）を持たない。

そこで、同じ図に描かれている行政界のベクタ線を N03（国土数値情報 行政区域）
と重ね合わせて変換式を推定する。手順は次の3段。

  1. ラベルの市町村コードから求めた市町村重心を使って初期アフィンを作る
     （3市町村未満しか無い県は、最大ポリゴンのバウンディングボックスで代用）
  2. 図の行政界頂点と N03 の行政界頂点で ICP（最近傍対応→アフィン再推定）
  3. 2次多項式で仕上げる（元の投影法が不明なため、円錐図法などの
     わずかな曲がりを多項式で吸収する）

最後に「点がラベルの市町村ポリゴンに入っているか」で検証する。
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.signal import fftconvolve
from scipy.spatial import cKDTree

# マーカーグリフの外接矩形中心から、実際に描かれる円の中心へのずれ（pt）。
# 600dpi で描画した凡例マーカーの塗り領域の重心から実測した。
MARKER_DX, MARKER_DY = -0.036, -0.609

MARKER_FONT = "ESRIDefaultMarker"
KM_PER_DEG = 111.0  # 緯度1度。誤差評価の桁を見るだけなので近似で足りる


# --------------------------------------------------------------------------
# PDF から要素を取り出す
# --------------------------------------------------------------------------
def stroked_paths(page):
    """線として描かれたパスを (線幅, 頂点配列) で返す。"""
    out = []
    for g in page.get_drawings():
        if g.get("type") != "s":
            continue
        pts = []
        for it in g["items"]:
            if it[0] == "l":
                pts += [(it[1].x, it[1].y), (it[2].x, it[2].y)]
            elif it[0] == "c":
                pts += [(p.x, p.y) for p in it[1:]]
            elif it[0] == "re":
                r = it[1]
                pts += [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
            elif it[0] == "qu":
                pts += [(p.x, p.y) for p in it[1]]
        if len(pts) >= 2:
            out.append((round(g.get("width") or 0, 2), np.asarray(pts, dtype=float)))
    return out


def _decoration(pts: np.ndarray, page) -> bool:
    """地図ではない装飾（外枠・凡例の箱）を弾く。

    行政界は2点ずつの短い線分に分かれて描かれるため、頂点数では判定できない。
    ページ端に接するパスを外枠、4点以上の軸平行な閉じたパスを凡例の箱とみなす。
    """
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    if x0 < 20 or y0 < 20 or x1 > page.rect.width - 20 or y1 > page.rect.height - 20:
        return True
    if len(pts) >= 4:
        d = np.abs(np.diff(pts, axis=0))
        if np.all((d[:, 0] < 0.05) | (d[:, 1] < 0.05)):
            return True
    return False


def boundary_cloud(page, max_pts: int = 60000):
    """都道府県界の頂点群と、地図本体の範囲を返す。

    図には線幅で3種類の線がある（例: 0.36pt=市区町村界, 1.08pt=都道府県界,
    3.96pt=図中の囲み）。合わせ込みには都道府県界＝海岸線と県境を使う。
    市区町村界は N03 との総描の差が大きく、かえって当てはまりが悪くなる。

    都道府県界は「ページのかなりの範囲に広がる線幅クラスのうち最も太いもの」
    として選ぶ。囲み枠のような局所的な線は範囲比で除ける。
    """
    cls: dict[float, list] = {}
    for w, p in stroked_paths(page):
        if not _decoration(p, page):
            cls.setdefault(w, []).append(p)
    if not cls:
        raise ValueError("行政界のパスが見つからない")

    page_area = page.rect.width * page.rect.height
    spread = {}
    for w, v in cls.items():
        a = np.vstack(v)
        spread[w] = float(a[:, 0].ptp() * a[:, 1].ptp() / page_area)
    wide = [w for w, s in spread.items() if s >= 0.4] or list(cls)
    pts = np.vstack(cls[max(wide)])

    allpts = np.vstack([p for v in cls.values() for p in v])
    bbox = (allpts[:, 0].min(), allpts[:, 1].min(), allpts[:, 0].max(), allpts[:, 1].max())
    if len(pts) > max_pts:  # ICP を軽くするため間引く（形は保たれる）
        pts = pts[np.linspace(0, len(pts) - 1, max_pts).astype(int)]
    return pts, bbox


def _spans(page):
    for b in page.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                yield "".join(c["c"] for c in s["chars"]), s


def markers(page):
    """井戸マーカー候補（凡例のマーカーも含む）を返す。"""
    out = []
    for t, s in _spans(page):
        if s["font"] == MARKER_FONT and t == "!":
            x0, y0, x1, y1 = s["bbox"]
            out.append(dict(x=(x0 + x1) / 2 + MARKER_DX, y=(y0 + y1) / 2 + MARKER_DY,
                            color=s["color"], size=round(s["size"], 1)))
    return out


def labels(page):
    """`整理番号 ,市町村コード ,使用目的` のラベルを返す。

    ArcMap は色を変える箇所でスパンを分けるため、同じ行で隣接するスパンを
    連結してから解析する。連結は「右隣にほぼ接している」場合だけ。
    y が 1pt 弱しか違わない別のラベルが左側にあることがあり、左向きの
    ギャップを許すとそれを吸い込んで解析できなくなる（青森など）。
    """
    # フォントは県によって違う（Arial-BoldMT のほか、点が多い富山などは
    # MS-UIGothic の小さい字）。書式で拾い、凡例の記載例だけ位置で除く。
    ban = [s["bbox"] for t, s in _spans(page) if "整理番号" in t]
    raw = []
    for t, s in _spans(page):
        bb = s["bbox"]
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        if any(b[0] - 45 <= cx <= b[2] + 45 and b[1] - 45 <= cy <= b[3] + 45 for b in ban):
            continue  # 凡例の「整理番号 ,市町村コード ,使用目的」の記載例
        raw.append((t, bb))
    raw.sort(key=lambda r: (round(r[1][1], 1), r[1][0]))
    joined, cur = [], None
    for t, bb in raw:
        if cur and abs(bb[1] - cur["y0"]) < 1.0 and -1.0 <= bb[0] - cur["x1"] < 2.0:
            cur["text"] += t
            cur["x1"] = bb[2]
        else:
            if cur:
                joined.append(cur)
            cur = dict(text=t, x0=bb[0], x1=bb[2], y0=bb[1], y1=bb[3])
    if cur:
        joined.append(cur)

    out = []
    for c in joined:
        parts = [p.strip() for p in c["text"].split(",")]
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            out.append(dict(seq=int(parts[0]), city=parts[1], use=int(parts[2]),
                            x=c["x0"], y=(c["y0"] + c["y1"]) / 2))
    return out


def scale_pt_per_km(page):
    """スケールバーの数字から 1km が何pt かを読む。

    バーの目盛は等間隔ではない（0 5 10 20 30 40 など）ので、
    数字の中心x座標と値で最小二乗を取る。図の実寸が判るので、
    合わせ込みで縮尺を自由変数にせずに済む。
    """
    nums, km = [], None
    for t, s in _spans(page):
        bb = s["bbox"]
        if t.strip() == "km":
            km = bb
        elif t.strip().isdigit():
            nums.append((int(t), (bb[0] + bb[2]) / 2, round(bb[1], 1)))
    if km is None:
        return None
    # 整理番号も数字なので、そのままでは混ざる（富山・静岡で誤読した）。
    # 目盛の数字は全て同じベースラインに並ぶので、行でまとめて選ぶ。
    rows: dict[float, list] = {}
    for v, x, y0 in nums:
        rows.setdefault(y0, []).append((v, x))
    ok = [(y0, r) for y0, r in rows.items()
          if len(r) >= 3 and abs(y0 - km[1]) < 14 and min(v for v, _ in r) == 0]
    if not ok:
        return None
    band = min(ok, key=lambda t: abs(t[0] - km[1]))[1]
    v = np.array([b[0] for b in band], dtype=float)
    x = np.array([b[1] for b in band], dtype=float)
    slope, _ = np.linalg.lstsq(np.column_stack([v, np.ones_like(v)]), x, rcond=None)[0]
    return float(slope) if slope > 0 else None


def _raster(pts, org, shape, cell):
    ix = ((pts[:, 0] - org[0]) / cell).astype(int)
    iy = ((pts[:, 1] - org[1]) / cell).astype(int)
    m = (ix >= 0) & (ix < shape[1]) & (iy >= 0) & (iy < shape[0])
    a = np.zeros(shape, dtype=np.float32)
    np.add.at(a, (iy[m], ix[m]), 1.0)
    return np.minimum(a, 1.0)


def align_translation(src_km, ref_km, cell=2.0):
    """位相相関で平行移動量を求める。縮尺が既知なら残る自由度はこれだけ。

    総当たりに近い探索なので局所解に落ちない。ICPだけで合わせようとすると
    図を縮めて線の上に乗せる解に落ちることがあり、それを避けるのが目的。
    """
    lo = np.minimum(src_km.min(axis=0), ref_km.min(axis=0)) - 20
    hi = np.maximum(src_km.max(axis=0), ref_km.max(axis=0)) + 20
    shape = (int((hi[1] - lo[1]) / cell) + 1, int((hi[0] - lo[0]) / cell) + 1)
    A = _raster(ref_km, lo, shape, cell)
    B = _raster(src_km, lo, shape, cell)
    corr = fftconvolve(A, B[::-1, ::-1], mode="same")
    iy, ix = np.unravel_index(int(np.argmax(corr)), corr.shape)
    return np.array([(ix - shape[1] // 2) * cell, (iy - shape[0] // 2) * cell])


def icp_translation(src, ref, t, iters=40, trim=0.9):
    """平行移動だけを繰り返し推定する（縮尺・回転は固定）。"""
    tree = cKDTree(ref)
    for _ in range(iters):
        d, idx = tree.query(src + t)
        keep = d < np.quantile(d, trim)
        t = t + (ref[idx[keep]] - (src[keep] + t)).mean(axis=0)
    d, _ = tree.query(src + t)
    return t, d


def use_colors(page):
    """凡例から マーカー色 -> 地下水使用目的コード の対応を読む。"""
    mk = markers(page)
    legend = [m for m in mk if m["size"] == max(m["size"] for m in mk)]
    out = {}
    for t, s in _spans(page):
        if s["font"].startswith("MS-") and len(t) > 2 and t[0].isdigit() and t[1] == "　":
            cy = (s["bbox"][1] + s["bbox"][3]) / 2
            cand = [m for m in legend if abs(m["y"] - cy) < 6 and m["x"] < s["bbox"][0]]
            if cand:
                out[min(cand, key=lambda m: s["bbox"][0] - m["x"])["color"]] = int(t[0])
    return out


# --------------------------------------------------------------------------
# 幾何変換
# --------------------------------------------------------------------------
def _fit_affine(src, dst):
    A = np.hstack([src, np.ones((len(src), 1))])
    return np.linalg.lstsq(A, dst, rcond=None)[0]  # (3, 2)


def apply_affine(M, src):
    return np.hstack([src, np.ones((len(src), 1))]) @ M


def _poly2_design(s):
    x, y = s[:, 0], s[:, 1]
    return np.column_stack([np.ones_like(x), x, y, x * x, x * y, y * y])


def apply_poly2(P, s):
    return _poly2_design(np.atleast_2d(s)) @ P


def init_from_labels(labs, centroid_of_code, pref):
    """ラベルの市町村重心を使った初期アフィン。3市町村未満なら None。"""
    src, dst = [], []
    for lb in labs:
        c = centroid_of_code.get(pref + lb["city"])
        if c is not None:
            src.append((lb["x"], lb["y"]))
            dst.append(c)
    if len({tuple(d) for d in dst}) < 3:
        return None
    return _fit_affine(np.array(src), np.array(dst))


def init_from_bbox(map_bbox, ref_bbox):
    """図の範囲と参照形状の範囲を合わせる初期アフィン（y は上下反転）。"""
    x0, y0, x1, y1 = map_bbox
    lon0, lat0, lon1, lat1 = ref_bbox
    sx = (lon1 - lon0) / (x1 - x0)
    sy = (lat1 - lat0) / (y1 - y0)
    return np.array([[sx, 0.0], [0.0, -sy], [lon0 - sx * x0, lat1 + sy * y0]])


def fit_fixed_scale(cloud, ref, ppk, lon0, lat0):
    """スケールバーで縮尺を固定し、平行移動だけを合わせる。

    実測（うまく合った県）では、図は「北上・緯度方向111km/度・経度方向は
    cos(緯度)補正」の等距円筒とみなせる。y方向の縮尺はスケールバーと
    0.2%以内で一致し、せん断はほぼ0だった。よって未知量は平行移動2つだけで、
    位相相関で総当たりに近く求められる。

    戻り値は pt -> (経度, 緯度) のアフィン行列。
    """
    cos = np.cos(np.radians(lat0))
    src_km = np.column_stack([cloud[:, 0] / ppk, -cloud[:, 1] / ppk])
    ref_km = np.column_stack([(ref[:, 0] - lon0) * KM_PER_DEG * cos,
                              (ref[:, 1] - lat0) * KM_PER_DEG])
    t = align_translation(src_km, ref_km)
    t, d = icp_translation(src_km, ref_km, t)
    M = np.array([[1.0 / (ppk * KM_PER_DEG * cos), 0.0],
                  [0.0, -1.0 / (ppk * KM_PER_DEG)],
                  [lon0 + t[0] / (KM_PER_DEG * cos), lat0 + t[1] / KM_PER_DEG]])
    return M, d / KM_PER_DEG  # 残差は度に直して返す


def scale_ok(M, ppk, lat0, tol=0.05):
    """アフィンの縮尺がスケールバーと整合しているか。ICPの潰れを弾く。"""
    cos = np.cos(np.radians(lat0))
    want_y = 1.0 / (ppk * KM_PER_DEG)
    want_x = want_y / cos
    return (abs(abs(M[1, 1]) / want_y - 1) < tol
            and abs(abs(M[0, 0]) / want_x - 1) < tol)


def icp(src, ref, M, iters=60, trim=0.9):
    """最近傍対応とアフィン再推定を繰り返す。"""
    tree = cKDTree(ref)
    for _ in range(iters):
        d, idx = tree.query(apply_affine(M, src))
        keep = d < np.quantile(d, trim)
        M = _fit_affine(src[keep], ref[idx[keep]])
    d, _ = tree.query(apply_affine(M, src))
    return M, d


def refine_poly2(src, ref, M, iters=30, trim=0.9):
    """2次多項式で仕上げる。"""
    tree = cKDTree(ref)
    P = None
    for _ in range(iters):
        cur = apply_affine(M, src) if P is None else apply_poly2(P, src)
        d, idx = tree.query(cur)
        keep = d < np.quantile(d, trim)
        P = np.linalg.lstsq(_poly2_design(src[keep]), ref[idx[keep]], rcond=None)[0]
    d, _ = tree.query(apply_poly2(P, src))
    return P, d


def pair_markers(labs, mks, color_use, outside_km=None, penalty=1e6, cap_km=3.0):
    """ラベルとマーカーを1対1に割り当てる。

    費用は3つ。
      距離           ラベルは対応するマーカーの近くに置かれる。ただし混雑した
                     場所では引き出し線で離れるため、これだけでは決まらない
      色の不一致     マーカー色は使用目的コードを表す（凡例から読む）
      市町村のはみ出し ラベルには市町村コードが書かれている。座標に直した
                     マーカーがその市町村からどれだけ外れるか（km）。
                     混雑した場所を解くのはこれが効く。
                     ただし上限を設ける。原本の市町村コード自体が誤っている行
                     （熊本の益城町/美里町など）は、どう割り当てても外れる。
                     飽和させないとその巨額の費用を減らそうとして、正しく
                     入っていた隣の点まで入れ替わってしまう

    マーカーの方が多い場合（凡例の分だけ多い）、余ったマーカーは割り当てられ
    ずに残る。凡例のマーカーはどの市町村にも入らないので自然に排除される。

    戻り値は (ラベルの添字, マーカーの添字, 費用) の並び。
    """
    C = np.empty((len(labs), len(mks)))
    for i, lb in enumerate(labs):
        for j, mk in enumerate(mks):
            d = float(np.hypot(mk["x"] - lb["x"], mk["y"] - lb["y"]))
            bad = color_use.get(mk["color"]) not in (None, lb["use"])
            far = 0.0 if outside_km is None else 1000.0 * min(outside_km[i, j], cap_km)
            C[i, j] = d + far + penalty * bad
    r, c = linear_sum_assignment(C)
    return [(int(i), int(j), float(C[i, j])) for i, j in zip(r, c)]
