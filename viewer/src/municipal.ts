/**
 * 自治体が公開している井戸データ（防災井戸・観測井）の重ね合わせ。
 *
 * F9（国交省・2003年版）とは出所も母集団も違う別のデータなので、主題（色分け）には
 * 混ぜず、独立したレイヤとして重ねる。既定は非表示で、チェックを入れたときに初めて
 * GeoJSON を取りに行く。
 *
 * F9 の点は丸、こちらは**ひし形**で描く。F9 側は主題によらず青が主役になり、
 * 空いている色相（violet）は dark で青と CVD 上ほぼ区別できない（ΔE 1.9）。
 * 色だけに頼らないよう、形をマーカーの一次の手がかりにしている。
 */
import maplibregl from 'maplibre-gl'
import type { ExpressionSpecification } from 'maplibre-gl'

import stylesJson from '../../data/viewer_styles.json'
import statsJson from '../../data/municipal_stats.json'
// ?url なので本文はバンドルに入らず、dist/assets/ のハッシュ付きファイルを
// MapLibre が自分で取りに行く。
import municipalUrl from '../../output/municipal_wells.geojson?url'
import { esc, fieldLinks } from './layers'
import type { Theme } from './theme'

interface PosSourceDef {
  v: string
  label: string
  hollow: boolean
}

const CFG = stylesJson.municipal as {
  label: string
  color: { light: string; dark: string }
  posSources: PosSourceDef[]
  attributeLabels: Record<string, string>
  popupOrder: string[]
}

export const MUNICIPAL_STATS = statsJson
export const MUNICIPAL_LABEL = CFG.label
export const MUNICIPAL_SRC = 'municipal'
export const MUNICIPAL_LYR = 'municipal-lyr'

/**
 * 出典表示。自治体名は data/municipal_stats.json の出典名（「船橋市 防災用井戸一覧」）の
 * 先頭語から作るので、対象データが増えても書き換えずに追随する。
 */
const ATTRIBUTION =
  '自治体の井戸データ（' +
  [...new Set(Object.keys(statsJson.bySource).map((s) => s.split(/[ 　]/)[0]))].join('・') +
  '）を加工'

const ICON_SOLID = 'muni-solid'
const ICON_HOLLOW = 'muni-hollow'
/**
 * アイコンの一辺（デバイスピクセル）。pixelRatio 2 で addImage するので見た目は半分。
 * icon-size で 1.0 を超えると拡大されて眠くなるので、いちばん大きく出す z16 が
 * ちょうど 1.0 になる大きさで描いておく。
 */
const ICON_PX = 72
/** ひし形の頂点が切れないよう空ける余白。90°の角はマイター継ぎが線幅の約1.4倍はみ出す。 */
const ICON_PAD = 8

const colorOf = (mode: Theme): string => (mode === 'dark' ? CFG.color.dark : CFG.color.light)
const surfaceOf = (mode: Theme): string =>
  mode === 'dark' ? stylesJson.surface.dark : stylesJson.surface.light

/**
 * ひし形のアイコンを描く。MapLibre の circle レイヤは丸しか出せないので、
 * 形で見分けさせるには symbol レイヤ＋自前の画像が要る。
 *
 * hollow は「原本に座標が無く、住所から解決した点」。F9 の市区町村代表点と同じく
 * 輪郭のみだが、番地まで解決できているので位置そのものは信用してよい（凡例で説明する）。
 */
function diamondImage(color: string, surface: string, hollow: boolean): ImageData | null {
  const c = document.createElement('canvas')
  c.width = c.height = ICON_PX
  const ctx = c.getContext('2d')
  if (!ctx) return null

  const m = ICON_PX / 2
  const r = m - ICON_PAD
  const path = new Path2D()
  path.moveTo(m, m - r)
  path.lineTo(m + r, m)
  path.lineTo(m, m + r)
  path.lineTo(m - r, m)
  path.closePath()

  if (hollow) {
    ctx.lineWidth = 8
    ctx.strokeStyle = color
    ctx.stroke(path)
  } else {
    ctx.fillStyle = color
    ctx.fill(path)
    // 点が重なっても粒が分かれて見えるよう、背景色の細いリングを回す（F9 の丸と同じ扱い）
    ctx.lineWidth = 3
    ctx.strokeStyle = surface
    ctx.stroke(path)
  }
  return ctx.getImageData(0, 0, ICON_PX, ICON_PX)
}

/** テーマが変わると色も変わるので、画像は貼り直す。 */
function addImages(map: maplibregl.Map, mode: Theme): void {
  const color = colorOf(mode)
  const surface = surfaceOf(mode)
  for (const [id, hollow] of [[ICON_SOLID, false], [ICON_HOLLOW, true]] as [string, boolean][]) {
    const img = diamondImage(color, surface, hollow)
    if (!img) continue
    if (map.hasImage(id)) map.updateImage(id, img)
    else map.addImage(id, img, { pixelRatio: 2 })
  }
}

/**
 * ズームに応じた大きさ。F9 の RADIUS と同じ勾配だが、一回り大きく出す。
 *
 * ひし形は同じ差し渡しの丸より面積が6割ほど小さく、並べると沈んで見える。
 * 数がF9の2桁下で、しかも自分でチェックを入れて出すレイヤなので、
 * 半対角が丸の半径の1.5〜1.6倍になるところまで上げている
 * （画像の半対角は size 1.0 で 14 CSS px）。
 */
const ICON_SIZE: ExpressionSpecification = [
  'interpolate', ['linear'], ['zoom'],
  4, 0.22, 8, 0.4, 12, 0.65, 16, 1,
] as unknown as ExpressionSpecification

function iconExpr(): ExpressionSpecification {
  const hollow = CFG.posSources.filter((p) => p.hollow).map((p) => p.v)
  return [
    'case',
    ['in', ['get', 'POS_SOURCE'], ['literal', hollow]], ICON_HOLLOW,
    ICON_SOLID,
  ] as unknown as ExpressionSpecification
}

/**
 * ソースとレイヤを（無ければ）追加する。スタイルを作り直したあとにも呼ばれるので、
 * 何度呼んでも副作用が無いようにしておく。visible が false のうちは GeoJSON を
 * 取りに行かせないよう、レイヤごと作らない。
 */
export function addMunicipalLayers(
  map: maplibregl.Map,
  mode: Theme,
  opacity: number,
  visible: boolean,
  /** これより下に挿す（選択リングを常に最前面に保つため） */
  beforeId?: string,
): void {
  if (!visible) return
  addImages(map, mode)
  if (!map.getSource(MUNICIPAL_SRC)) {
    // 出典はソースに持たせる。重ね合わせを消したときに表示からも消えてほしいため
    // （地図の attribution 欄はソースの増減に追随する）。
    map.addSource(MUNICIPAL_SRC, {
      type: 'geojson',
      data: municipalUrl,
      attribution: ATTRIBUTION,
    })
  }
  if (!map.getLayer(MUNICIPAL_LYR)) {
    map.addLayer(
      {
        id: MUNICIPAL_LYR,
        type: 'symbol',
        source: MUNICIPAL_SRC,
        layout: {
          'icon-image': iconExpr(),
          'icon-size': ICON_SIZE,
          // 密なところでも間引かせない。ラベルではなくデータ点なので、消えるほうが困る。
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
        },
        paint: { 'icon-opacity': opacity },
      },
      beforeId && map.getLayer(beforeId) ? beforeId : undefined,
    )
  }
}

/** チェックを外したときは消す。次に入れたら addMunicipalLayers で作り直す。 */
export function removeMunicipalLayers(map: maplibregl.Map): void {
  if (map.getLayer(MUNICIPAL_LYR)) map.removeLayer(MUNICIPAL_LYR)
  if (map.getSource(MUNICIPAL_SRC)) map.removeSource(MUNICIPAL_SRC)
}

export function repaintMunicipal(map: maplibregl.Map, mode: Theme, opacity: number): void {
  if (!map.getLayer(MUNICIPAL_LYR)) return
  addImages(map, mode)
  map.setPaintProperty(MUNICIPAL_LYR, 'icon-opacity', opacity)
}

// ---- 凡例 ----

export interface MunicipalLegendItem {
  label: string
  color: string
  hollow: boolean
  n: number
}

export function municipalLegend(mode: Theme): MunicipalLegendItem[] {
  const counts = MUNICIPAL_STATS.byPosSource as Record<string, number>
  return CFG.posSources.map((p) => ({
    label: p.label,
    color: colorOf(mode),
    hollow: p.hollow,
    n: counts[p.v] ?? 0,
  }))
}

// ---- ポップアップ ----

/** 深度は数値のときだけ単位を付ける（原本が空欄や「不明」のこともある）。 */
function fmt(key: string, raw: string): string {
  if (key === 'DEPTH_M' && Number.isFinite(Number(raw))) return `${esc(raw)} m`
  return esc(raw)
}

export function municipalPopupHtml(p: Record<string, unknown>, lat: number, lon: number): string {
  const title = String(p.NAME ?? '').trim() || String(p.SOURCE_NAME ?? '') || '名称の記載なし'
  const rows: string[] = []
  for (const k of CFG.popupOrder) {
    // 名称は見出しに出しているので表では繰り返さない
    if (k === 'NAME') continue
    const v = p[k]
    if (v === undefined || v === null || String(v).trim() === '') continue
    rows.push(`<dt>${esc(CFG.attributeLabels[k] ?? k)}</dt><dd>${fmt(k, String(v))}</dd>`)
  }
  return (
    `<div class="pp-title">${esc(title)}</div>` +
    `<div class="pp-badge pp-q-municipal">${esc(CFG.label)}</div>` +
    fieldLinks(lat, lon) +
    (rows.length ? `<dl class="pp-dl">${rows.join('')}</dl>` : '') +
    '<p class="pp-foot">F9（国交省・2003年版）とは別のデータです。深度を公開しているのは船橋市だけで、' +
    'ほかは災害時協力井戸・観測井の位置一覧です。</p>'
  )
}
