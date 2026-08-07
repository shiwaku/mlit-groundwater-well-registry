import type { ExpressionSpecification, FilterSpecification } from 'maplibre-gl'
// 配色・凡例・属性和名はリポジトリ直下の data/viewer_styles.json が唯一の出所。
// 色を変えるときはここではなく JSON を直す。
import stylesJson from '../../data/viewer_styles.json'
// 母数の集計は scripts/14_build_pmtiles.py が出す。低ズームでは点が間引かれるため、
// 「全体で何件か」を描画中のフィーチャ数から数えてはいけない。
import statsJson from '../../data/viewer_stats.json'
import type { Theme } from './theme'

export const STATS = statsJson
export const CODE_LABELS = stylesJson.codeLabels as Record<string, Record<string, string>>
export const DEFAULT_OPACITY = stylesJson.defaultOpacity

/** 位置精度の3区分。凡例・フィルタ・ポップアップで共通に使う。 */
export const POS_QUALITIES = ['unique', 'site', 'fallback'] as const
export type PosQuality = (typeof POS_QUALITIES)[number]

/**
 * 既定で表示する位置精度。`unique`（実位置・単独の座標）36,415点だけを出す。
 *
 * 全57,847点のうち16,081点は市区町村代表点、5,351点は同一サイトへの相乗りで、
 * 初見の人が地図を開いたときに「点がある＝そこに井戸がある」と読んでしまう。
 * 既定を「そのまま信用してよい点」に揃えて、残りはチェックボックスで足してもらう。
 * どの主題（色分け）で見ていても位置精度の絞り込みは共通に効く。
 */
export const DEFAULT_POSQ: readonly PosQuality[] = ['unique']

// ---- テーマ（色分けの主題） ----

interface CatDef {
  v: string
  label: string
  light: string
  dark: string
  /** true なら塗らずに輪郭だけで描く（色以外の手がかりを足すため） */
  hollow?: boolean
}
interface NoDataDef {
  label: string
  light: string
  dark: string
}
interface CatsTheme {
  key: string
  name: string
  desc: string
  kind: 'cats'
  prop: string
  cats: CatDef[]
  /** cats に無い値の受け皿。取り得る値を cats が尽くしているテーマでは持たない。 */
  fallback?: NoDataDef
}
interface StepsTheme {
  key: string
  name: string
  desc: string
  kind: 'steps'
  prop: string
  unit: string
  /** これ未満は欠測扱い（-1 や 0 が欠測を意味するため） */
  validMin: number
  steps: number[]
  labels: string[]
  light: string[]
  dark: string[]
  nodata: NoDataDef
}
export type ThemeDef = CatsTheme | StepsTheme

export const THEMES = stylesJson.themes as unknown as ThemeDef[]
export const DEFAULT_THEME = stylesJson.defaultTheme

export function themeOf(key: string): ThemeDef {
  return THEMES.find((t) => t.key === key) ?? THEMES[0]
}

const pick = (c: { light: string; dark: string }, m: Theme): string => (m === 'dark' ? c.dark : c.light)

/**
 * 「市区町村代表点（fallback）は塗らずに輪郭だけで描く」を全テーマで効かせるための判定。
 *
 * 位置精度テーマでは配色の CVD 検証が light で floor 帯（ΔE 7.2）に入るため、色以外の
 * 手がかりが要る。それとは別に、どの主題で見ているときでも「この点の位置は信用できない」
 * ことは常に見えていたほうがよいので、テーマによらず輪郭のみにしている。
 */
const IS_FALLBACK: ExpressionSpecification = ['==', ['get', 'POS_QUALITY'], 'fallback']

/** 主題の値 → 色。欠測は muted グレーに落とす。 */
export function colorExpr(def: ThemeDef, mode: Theme): ExpressionSpecification {
  if (def.kind === 'cats') {
    const cases: string[] = []
    for (const c of def.cats) cases.push(c.v, pick(c, mode))
    const other = def.fallback ? pick(def.fallback, mode) : stylesJson.muted
    return ['match', ['to-string', ['get', def.prop]], ...cases, other] as unknown as ExpressionSpecification
  }
  const v: ExpressionSpecification = ['coalesce', ['get', def.prop], -1e9]
  const stepArgs: (number | string)[] = [def[mode][0]]
  for (let i = 0; i < def.steps.length; i++) stepArgs.push(def.steps[i], def[mode][i + 1])
  return [
    'case',
    ['<', v, def.validMin], pick(def.nodata, mode),
    ['step', v, ...stepArgs],
  ] as unknown as ExpressionSpecification
}

/** 塗り。fallback だけ透明にして輪郭のみにする。 */
export function fillExpr(def: ThemeDef, mode: Theme): ExpressionSpecification {
  return ['case', IS_FALLBACK, 'rgba(0,0,0,0)', colorExpr(def, mode)] as unknown as ExpressionSpecification
}

/**
 * 輪郭。fallback は主題色そのもの（塗りが無いのでこれが唯一の見え方）、
 * それ以外は背景色の細いリングにして、点が重なっても粒が分かれて見えるようにする。
 */
export function strokeColorExpr(def: ThemeDef, mode: Theme): ExpressionSpecification {
  const surface = mode === 'dark' ? stylesJson.surface.dark : stylesJson.surface.light
  return ['case', IS_FALLBACK, colorExpr(def, mode), surface] as unknown as ExpressionSpecification
}

export const STROKE_WIDTH: ExpressionSpecification = [
  'interpolate', ['linear'], ['zoom'],
  6, ['case', IS_FALLBACK, 0.8, 0.2],
  12, ['case', IS_FALLBACK, 1.6, 0.8],
] as unknown as ExpressionSpecification

/** 点の半径。ズームが浅いうちは小さく、寄るほど大きく。 */
export const RADIUS: ExpressionSpecification = [
  'interpolate', ['linear'], ['zoom'],
  4, 1.6, 8, 3, 12, 5.5, 16, 9,
] as unknown as ExpressionSpecification

/** 選択した点を囲むリングの半径。RADIUS より一回り大きい。 */
export const HL_RADIUS: ExpressionSpecification = [
  'interpolate', ['linear'], ['zoom'],
  4, 6, 8, 8, 12, 11, 16, 15,
] as unknown as ExpressionSpecification

// ---- 凡例 ----

export interface LegendItem {
  /** フィルタの識別子。cats なら属性値、steps なら段の番号（'0'..）または 'nodata' */
  id: string
  label: string
  color: string
  hollow: boolean
}

export function legendFor(def: ThemeDef, mode: Theme): LegendItem[] {
  if (def.kind === 'cats') {
    const items = def.cats.map((c) => ({
      id: c.v, label: c.label, color: pick(c, mode), hollow: !!c.hollow,
    }))
    if (!def.fallback) return items
    // 「記載なし」は最後に置く
    return [...items, { id: '__other__', label: def.fallback.label, color: pick(def.fallback, mode), hollow: false }]
  }
  const items = def.labels.map((label, i) => ({ id: String(i), label, color: def[mode][i], hollow: false }))
  return [...items, { id: 'nodata', label: def.nodata.label, color: pick(def.nodata, mode), hollow: false }]
}

// ---- フィルタ ----

export interface FilterState {
  /** 表示する位置精度 */
  posq: Set<string>
  /** ADR_DIST_KM の上限（km）。null なら制限なし */
  maxDist: number | null
  /** ADR_DIST_KM が判定不能（-1）の点を含めるか */
  includeUnknownDist: boolean
  /** 掘削深度の下限（m）。0 なら制限なし */
  minDep: number
  /** 凡例クリックで単独表示にしている項目（主題ごと）。null なら全表示 */
  isolate: string | null
}

export function initialFilter(): FilterState {
  return {
    posq: new Set<string>(DEFAULT_POSQ),
    maxDist: null,
    includeUnknownDist: true,
    minDep: 0,
    isolate: null,
  }
}

/** 凡例の1項目だけを残す式。isolate 用。 */
function isolateExpr(def: ThemeDef, id: string): ExpressionSpecification | null {
  if (def.kind === 'cats') {
    if (id === '__other__') {
      const known = def.cats.map((c) => c.v)
      return ['!', ['in', ['to-string', ['get', def.prop]], ['literal', known]]] as unknown as ExpressionSpecification
    }
    return ['==', ['to-string', ['get', def.prop]], id] as unknown as ExpressionSpecification
  }
  const v: ExpressionSpecification = ['coalesce', ['get', def.prop], -1e9]
  if (id === 'nodata') return ['<', v, def.validMin] as unknown as ExpressionSpecification
  const i = Number(id)
  const lo = i === 0 ? def.validMin : def.steps[i - 1]
  const hi = i < def.steps.length ? def.steps[i] : null
  const conds: ExpressionSpecification[] = [['>=', v, lo] as unknown as ExpressionSpecification]
  if (hi !== null) conds.push(['<', v, hi] as unknown as ExpressionSpecification)
  return ['all', ...conds] as unknown as ExpressionSpecification
}

export function filterExpr(def: ThemeDef, f: FilterState): FilterSpecification {
  const parts: ExpressionSpecification[] = []

  parts.push(['in', ['get', 'POS_QUALITY'], ['literal', [...f.posq]]] as unknown as ExpressionSpecification)

  if (f.maxDist !== null) {
    // ADR_DIST_KM_N は判定不能を -1 で持っている
    const d: ExpressionSpecification = ['coalesce', ['get', 'ADR_DIST_KM_N'], -1]
    const within: ExpressionSpecification = ['all', ['>=', d, 0], ['<=', d, f.maxDist]] as unknown as ExpressionSpecification
    parts.push(
      (f.includeUnknownDist
        ? ['any', ['<', d, 0], within]
        : within) as unknown as ExpressionSpecification,
    )
  } else if (!f.includeUnknownDist) {
    parts.push(['>=', ['coalesce', ['get', 'ADR_DIST_KM_N'], -1], 0] as unknown as ExpressionSpecification)
  }

  if (f.minDep > 0) {
    parts.push(['>=', ['coalesce', ['get', 'DEP'], -1e9], f.minDep] as unknown as ExpressionSpecification)
  }

  if (f.isolate) {
    const e = isolateExpr(def, f.isolate)
    if (e) parts.push(e)
  }

  return ['all', ...parts] as unknown as FilterSpecification
}

/**
 * 位置精度フィルタだけを反映した母数。
 * 距離・深度のフィルタは行ごとの値が手元に無いので数えられない。数えられないものを
 * 推定して出すより、位置精度の内訳だけを正確に出すほうがよい。
 */
export function selectedTotal(f: FilterState): number {
  const c = STATS.posQuality as Record<string, number>
  return [...f.posq].reduce((a, k) => a + (c[k] ?? 0), 0)
}

// ---- ポップアップ ----

const ATTR_LABELS = stylesJson.attributeLabels as Record<string, string>
const ZERO_IS_MISSING = new Set(stylesJson.zeroIsMissing)
const DATE_FIELDS = new Set(stylesJson.dateFields)
const POPUP_ORDER = stylesJson.popupOrder as string[]

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] as string)
}

const S = (p: Record<string, unknown>, k: string): string => {
  const v = p[k]
  return v === undefined || v === null || v === '' ? '' : String(v)
}

/** YYYYMMDD → YYYY-MM-DD。0 / 00000000 は原本の「未測定」プレースホルダ。 */
function fmtDate(raw: string): string {
  const s = raw.replace(/\.0$/, '')
  if (!s || /^0+$/.test(s)) return '未測定'
  const m = /^(\d{4})(\d{2})(\d{2})$/.exec(s)
  if (!m) return s
  const [, y, mo, d] = m
  if (mo === '00' && d === '00') return y
  if (d === '00') return `${y}-${mo}`
  return `${y}-${mo}-${d}`
}

/**
 * 値の整形。0埋め列の 0 には ※ を付けるだけにして、説明は表の下に1行でまとめる。
 * 狭い値の列に注記を入れると数値まで折り返されて読めなくなるため。
 * 戻り値の2番目が true なら「※」を使った＝脚注を出す。
 */
function fmtValue(key: string, v: unknown): [string, boolean] {
  let s = String(v)
  if (DATE_FIELDS.has(key)) return [esc(fmtDate(s)), false]
  // float で入っている整数（1.0 → 1）を素直に見せる
  if (typeof v === 'number' && Number.isInteger(v)) s = String(v)
  const label = CODE_LABELS[key]?.[s]
  if (label) return [esc(label), false]
  if (ZERO_IS_MISSING.has(key) && Number(s) === 0) {
    return ['0 <span class="pp-mark">※</span>', true]
  }
  return [esc(s), false]
}

/** 現地確認用の外部リンク。座標は原本の値（LAT/LON 属性）をそのまま使う。 */
function fieldLinks(lat: number, lon: number): string {
  const q = `${lat.toFixed(6)},${lon.toFixed(6)}`
  const links: [string, string, string][] = [
    ['Google マップ', `https://www.google.com/maps/search/?api=1&query=${q}`, '🗺️'],
    ['ストリートビュー', `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${q}`, '👁️'],
    ['地理院地図', `https://maps.gsi.go.jp/#17/${lat.toFixed(6)}/${lon.toFixed(6)}/`, '📍'],
  ]
  return (
    '<div class="pp-links">' +
    links.map(([t, u, i]) => `<a href="${u}" target="_blank" rel="noopener"><span aria-hidden="true">${i}</span>${t}</a>`).join('') +
    '</div>'
  )
}

const QUALITY_SHORT: Record<string, string> = {
  unique: '実位置（単独の座標）',
  site: '実位置（同一サイト）',
  fallback: '市区町村代表点（実位置ではない）',
}

export function popupHtml(p: Record<string, unknown>, lat: number, lon: number): string {
  const title = [S(p, 'PREF_NAME'), S(p, 'ADR')].filter(Boolean).join(' ') || '所在地の記載なし'
  const q = S(p, 'POS_QUALITY')
  const badge = q
    ? `<div class="pp-badge pp-q-${esc(q)}">${esc(QUALITY_SHORT[q] ?? q)}</div>`
    : ''

  // ストリートビューは「実位置ではない」点でも開けてしまうので、開く前に一言添える
  const warn = q === 'fallback'
    ? '<p class="pp-warn">この点は井戸の位置ではなく市区町村の代表点です。現地確認の起点には使えません。</p>'
    : ''

  const seen = new Set<string>()
  const rows: string[] = []
  let hasZeroMark = false
  const add = (k: string): void => {
    if (seen.has(k)) return
    seen.add(k)
    const v = p[k]
    if (v === undefined || v === null || v === '') return
    const [html, marked] = fmtValue(k, v)
    if (marked) hasZeroMark = true
    rows.push(`<dt>${esc(ATTR_LABELS[k] ?? k)}</dt><dd>${html}</dd>`)
  }
  for (const k of POPUP_ORDER) add(k)
  // 定義に無い列が増えても落とさない（_N は表示用に足した派生列なので出さない）
  for (const k of Object.keys(p)) if (!k.endsWith('_N')) add(k)

  return (
    `<div class="pp-title">${esc(title)}</div>` +
    badge +
    fieldLinks(lat, lon) +
    warn +
    (rows.length ? `<dl class="pp-dl">${rows.join('')}</dl>` : '') +
    (hasZeroMark
      ? '<p class="pp-foot">※ シェープファイル化のときに空欄が 0 で埋められた列です。真の 0 と区別できません。</p>'
      : '')
  )
}
