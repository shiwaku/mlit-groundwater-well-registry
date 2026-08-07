import maplibregl from 'maplibre-gl'
import { Protocol } from 'pmtiles'
import 'maplibre-gl/dist/maplibre-gl.css'

import { getBasemapStyle, type Basemap } from './basemap'
import {
  DEFAULT_OPACITY,
  DEFAULT_THEME,
  HL_RADIUS,
  POS_QUALITIES,
  RADIUS,
  STATS,
  STROKE_WIDTH,
  THEMES,
  fillExpr,
  filterExpr,
  initialFilter,
  legendFor,
  popupHtml,
  selectedTotal,
  strokeColorExpr,
  themeOf,
  type FilterState,
} from './layers'
import { applyThemeAttr, initialTheme, type Theme } from './theme'
import './style.css'

const PMTILES_BASE = import.meta.env.VITE_PMTILES_BASE ?? '/pmtiles'
const PMTILES_FILE = 'f9_wells_2003.pmtiles'
const SRC = 'wells'
const SRC_LAYER = 'wells'
const LYR = 'wells-lyr'
const HL_SRC = 'click-highlight'
const HL_LYR = 'click-highlight-lyr'

const DATA_ATTRIBUTION =
  '全国地下水資料台帳調査（<a href="https://nlftp.mlit.go.jp/kokjo/inspect/landclassification/water/f9_exp.html" target="_blank" rel="noopener">国土交通省</a>）を加工'

let theme: Theme = initialTheme()
let base: Basemap = 'pale'
let themeKey: string = DEFAULT_THEME
let opacity = DEFAULT_OPACITY
const filter: FilterState = initialFilter()
applyThemeAttr(theme)

const isMobile = window.matchMedia('(max-width: 640px)').matches

const protocol = new Protocol()
maplibregl.addProtocol('pmtiles', protocol.tile)

const map = new maplibregl.Map({
  container: 'map',
  style: getBasemapStyle(base, theme),
  center: [137.5, 36.2],
  zoom: 5,
  hash: true,
  attributionControl: false,
  // モバイルは GPU/メモリが限られるため保持タイル数と描画バッファを絞る
  maxTileCacheSize: isMobile ? 32 : undefined,
  pixelRatio: isMobile ? Math.min(window.devicePixelRatio || 1, 2) : undefined,
})
map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), 'top-right')
map.addControl(
  new maplibregl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true },
    trackUserLocation: true,
    showUserLocation: true,
  }),
  'top-right',
)
map.addControl(new maplibregl.ScaleControl(), 'bottom-left')
map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: DATA_ATTRIBUTION }))

// ---- データ層 ----

function addDataLayers(): void {
  if (!map.getSource(HL_SRC)) {
    map.addSource(HL_SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
  }
  if (!map.getSource(SRC)) {
    map.addSource(SRC, { type: 'vector', url: `pmtiles://${PMTILES_BASE}/${PMTILES_FILE}` })
  }
  if (!map.getLayer(LYR)) {
    const def = themeOf(themeKey)
    map.addLayer({
      id: LYR,
      type: 'circle',
      source: SRC,
      'source-layer': SRC_LAYER,
      filter: filterExpr(def, filter),
      paint: {
        'circle-radius': RADIUS,
        'circle-color': fillExpr(def, theme),
        'circle-opacity': opacity,
        'circle-stroke-color': strokeColorExpr(def, theme),
        'circle-stroke-width': STROKE_WIDTH,
        'circle-stroke-opacity': opacity,
      },
    })
  }
  // 選択した点のハイライトは常に最前面。
  // circle-radius に ['+', RADIUS, 5] は書けない（zoom 式は step/interpolate の
  // 直下にしか置けない）ので、太らせた interpolate を別に持つ。
  if (!map.getLayer(HL_LYR)) {
    map.addLayer({
      id: HL_LYR,
      type: 'circle',
      source: HL_SRC,
      paint: {
        'circle-radius': HL_RADIUS,
        'circle-color': 'rgba(0,0,0,0)',
        'circle-stroke-color': 'rgba(250,178,25,1)',
        'circle-stroke-width': 3,
      },
    })
  }
}

function repaint(): void {
  if (!map.getLayer(LYR)) return
  const def = themeOf(themeKey)
  map.setFilter(LYR, filterExpr(def, filter))
  map.setPaintProperty(LYR, 'circle-color', fillExpr(def, theme))
  map.setPaintProperty(LYR, 'circle-stroke-color', strokeColorExpr(def, theme))
  map.setPaintProperty(LYR, 'circle-opacity', opacity)
  map.setPaintProperty(LYR, 'circle-stroke-opacity', opacity)
}

function setHighlight(f: maplibregl.MapGeoJSONFeature | null): void {
  const src = map.getSource(HL_SRC) as maplibregl.GeoJSONSource | undefined
  if (!src) return
  src.setData(
    f
      ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: f.geometry, properties: {} }] }
      : { type: 'FeatureCollection', features: [] },
  )
}

// ---- テーマ（ライト/ダーク） ----

const themeBtn = document.getElementById('theme-btn') as HTMLButtonElement
const renderThemeBtn = (): void => {
  themeBtn.textContent = theme === 'dark' ? '☀️' : '🌙'
}
// ラスタ（写真・標準）↔ ベクタ（淡色）の切替では diff 適用が効かないため diff:false で
// 完全に作り直す。setStyle 直後は isStyleLoaded() が旧スタイルで true を返して競合するので、
// 新スタイルが落ち着く idle を待ってからデータ層を貼り直す。
function reloadStyle(): void {
  map.setStyle(getBasemapStyle(base, theme), { diff: false })
  map.once('idle', () => addDataLayers())
}
themeBtn.addEventListener('click', () => {
  theme = theme === 'dark' ? 'light' : 'dark'
  applyThemeAttr(theme)
  renderThemeBtn()
  renderLegend()
  reloadStyle()
})

// ---- パネル開閉 ----

const panel = document.getElementById('panel') as HTMLElement
const collapseBtn = document.getElementById('collapse-btn') as HTMLButtonElement
const renderCollapseBtn = (): void => {
  collapseBtn.textContent = panel.classList.contains('collapsed') ? '▾' : '▴'
}
collapseBtn.addEventListener('click', () => {
  panel.classList.toggle('collapsed')
  renderCollapseBtn()
})

// ---- 主題（色分け）の切替 ----

const themesDiv = document.getElementById('themes') as HTMLElement
const themeDescEl = document.getElementById('theme-desc') as HTMLElement

function buildThemeButtons(): void {
  for (const def of THEMES) {
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.className = 'theme-btn'
    btn.textContent = def.name
    btn.dataset.key = def.key
    btn.setAttribute('aria-pressed', String(def.key === themeKey))
    btn.addEventListener('click', () => setActiveTheme(def.key))
    themesDiv.append(btn)
  }
}

function setActiveTheme(key: string): void {
  themeKey = key
  filter.isolate = null
  for (const b of themesDiv.querySelectorAll<HTMLButtonElement>('.theme-btn')) {
    b.setAttribute('aria-pressed', String(b.dataset.key === key))
  }
  themeDescEl.textContent = themeOf(key).desc
  renderLegend()
  repaint()
}

// ---- 凡例（クリックで単独表示） ----

const legendEl = document.getElementById('legend') as HTMLElement

function renderLegend(): void {
  const def = themeOf(themeKey)
  legendEl.innerHTML = ''
  for (const it of legendFor(def, theme)) {
    // 位置精度テーマの凡例は絞り込みのチェックボックスと同じ区分を指しているので、
    // 隠している区分の行を押しても何も出ない。行を淡くして、押したら表示に戻す。
    const hiddenPosq = def.key === 'posq' && !filter.posq.has(it.id)
    const row = document.createElement('button')
    row.type = 'button'
    row.className = hiddenPosq ? 'lg-row lg-row-off' : 'lg-row'
    row.setAttribute('aria-pressed', String(!hiddenPosq && filter.isolate === it.id))
    if (hiddenPosq) row.title = '絞り込みで非表示にしています。押すと表示に戻します。'
    const sw = document.createElement('span')
    sw.className = it.hollow ? 'lg-sw lg-sw-hollow' : 'lg-sw'
    if (it.hollow) sw.style.borderColor = it.color
    else sw.style.background = it.color
    const label = document.createElement('span')
    label.textContent = it.label
    row.append(sw, label)
    row.addEventListener('click', () => {
      if (hiddenPosq) {
        filter.posq.add(it.id)
        syncPosqChecks()
      } else {
        filter.isolate = filter.isolate === it.id ? null : it.id
      }
      renderLegend()
      repaint()
      renderCount()
    })
    legendEl.append(row)
  }
  // 主題が位置精度でなくても輪郭のみ＝fallback は効いているので、その説明を添える。
  // ただし fallback を隠しているときは指すものが無いので出さない。
  if (def.key !== 'posq' && filter.posq.has('fallback')) {
    const note = document.createElement('p')
    note.className = 'hint hint-sub'
    note.textContent = '輪郭のみの点は市区町村代表点（実位置ではない）です。'
    legendEl.append(note)
  }
}

// ---- 絞り込み ----

const posqDiv = document.getElementById('posq-filters') as HTMLElement
const POSQ_LABEL: Record<string, string> = {
  unique: '実位置（単独の座標）',
  site: '実位置（同一サイト）',
  fallback: '市区町村代表点',
}

function buildPosqFilters(): void {
  const counts = STATS.posQuality as Record<string, number>
  for (const q of POS_QUALITIES) {
    const label = document.createElement('label')
    label.className = 'check'
    const cb = document.createElement('input')
    cb.type = 'checkbox'
    cb.dataset.q = q
    cb.checked = filter.posq.has(q)
    cb.addEventListener('change', () => {
      if (cb.checked) filter.posq.add(q)
      else filter.posq.delete(q)
      renderLegend()
      repaint()
      renderCount()
    })
    const t = document.createElement('span')
    t.textContent = `${POSQ_LABEL[q]}（${(counts[q] ?? 0).toLocaleString()}点）`
    label.append(cb, t)
    posqDiv.append(label)
  }
}

/** チェックボックスの見た目を filter.posq に合わせ直す（凡例やリセットから変えたとき）。 */
function syncPosqChecks(): void {
  for (const cb of posqDiv.querySelectorAll<HTMLInputElement>('input')) {
    cb.checked = filter.posq.has(cb.dataset.q as string)
  }
}

// スライダーの目盛り。0.25km 刻みでは端が使いにくいので、実用的な値を並べる。
const DIST_STOPS = [0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15, 20, 30, 50]

const distRange = document.getElementById('dist-range') as HTMLInputElement
const distVal = document.getElementById('dist-val') as HTMLElement
const distUnknown = document.getElementById('dist-unknown') as HTMLInputElement
const depRange = document.getElementById('dep-range') as HTMLInputElement
const depVal = document.getElementById('dep-val') as HTMLElement

distRange.max = String(DIST_STOPS.length)
// 「判定不能」だと位置が怪しい点に読めるが、実際は町字を特定できず距離を測れなかった
// だけで座標の質とは別物。何ができなかったのかを書く。
;(document.getElementById('dist-unknown-label') as HTMLElement).textContent =
  `町名と突合できなかった点（${(STATS.total - STATS.distResolved).toLocaleString()}点）も表示する`

// 距離の上限・深度は母数を数えられないので、動かすと件数表示が「最大 n」に変わる。
// どれを動かしても renderCount() を通す。
function syncDist(): void {
  const i = Number(distRange.value)
  filter.maxDist = i >= DIST_STOPS.length ? null : DIST_STOPS[i]
  distVal.textContent = filter.maxDist === null ? '制限なし' : `${filter.maxDist}km 以内`
  repaint()
  renderCount()
}
distRange.addEventListener('input', syncDist)
distUnknown.addEventListener('change', () => {
  filter.includeUnknownDist = distUnknown.checked
  repaint()
  renderCount()
})
depRange.addEventListener('input', () => {
  filter.minDep = Number(depRange.value)
  depVal.textContent = filter.minDep === 0 ? '制限なし' : `${filter.minDep}m 以上`
  repaint()
  renderCount()
})

;(document.getElementById('filter-reset') as HTMLButtonElement).addEventListener('click', () => {
  // 初期状態の定義は initialFilter() ひとつに置く（既定の位置精度もそこ）
  Object.assign(filter, initialFilter())
  syncFilterUi()
  renderLegend()
  repaint()
  renderCount()
})

/** 入力欄の見た目を filter に合わせ直す。初期化とリセットで共用する。 */
function syncFilterUi(): void {
  syncPosqChecks()
  distUnknown.checked = filter.includeUnknownDist
  distRange.value = String(filter.maxDist === null ? DIST_STOPS.length : DIST_STOPS.indexOf(filter.maxDist))
  distVal.textContent = filter.maxDist === null ? '制限なし' : `${filter.maxDist}km 以内`
  depRange.value = String(filter.minDep)
  depVal.textContent = filter.minDep === 0 ? '制限なし' : `${filter.minDep}m 以上`
}

// ---- 件数・ズームの注記 ----

const countEl = document.getElementById('count') as HTMLElement
const zoomNoteEl = document.getElementById('zoom-note') as HTMLElement

function renderCount(): void {
  const { n, exact } = selectedTotal(filter)
  countEl.innerHTML =
    `表示中 <b>${exact ? '' : '最大 '}${n.toLocaleString()}</b> / 全 ${STATS.total.toLocaleString()} 点` +
    (exact ? '' : '<span class="pp-note">（距離の上限・深度・凡例での単独表示で絞ったぶんは数えていません）</span>')
}

function renderZoomNote(): void {
  const z = map.getZoom()
  zoomNoteEl.textContent =
    z >= STATS.completeFromZoom
      ? `ズーム${STATS.completeFromZoom}以上なので全点を描画しています。`
      : `ズーム${STATS.completeFromZoom}未満では密なところが間引かれます（データの欠落ではありません）。`
}

// ---- 背景地図スイッチャー（右下） ----

class BasemapControl implements maplibregl.IControl {
  private el!: HTMLElement
  onAdd(): HTMLElement {
    this.el = document.createElement('div')
    this.el.className = 'maplibregl-ctrl basemap-switch'
    const defs: [Basemap, string][] = [
      ['pale', '淡色'],
      ['photo', '写真'],
    ]
    for (const [b, label] of defs) {
      const btn = document.createElement('button')
      btn.type = 'button'
      btn.textContent = label
      btn.dataset.base = b
      btn.setAttribute('aria-selected', String(b === base))
      btn.addEventListener('click', () => setBase(b))
      this.el.append(btn)
    }
    return this.el
  }
  onRemove(): void {
    this.el.remove()
  }
  sync(): void {
    for (const btn of this.el.querySelectorAll<HTMLButtonElement>('button')) {
      btn.setAttribute('aria-selected', String(btn.dataset.base === base))
    }
  }
}
const basemapCtrl = new BasemapControl()
map.addControl(basemapCtrl, 'bottom-right')

function setBase(next: Basemap): void {
  if (next === base) return
  base = next
  basemapCtrl.sync()
  reloadStyle()
}

// ---- ホバー / クリック ----

if (window.matchMedia('(hover: hover)').matches) {
  map.on('mousemove', (e) => {
    if (!map.getLayer(LYR)) return
    const hit = map.queryRenderedFeatures(e.point, { layers: [LYR] }).length > 0
    map.getCanvas().style.cursor = hit ? 'pointer' : ''
  })
}

/**
 * ポップアップがパネルや画面端に隠れないよう、必要なぶんだけ地図をずらす。
 * MapLibre のポップアップは自動パンしない。開く前に見積もると、実際にどちら側に
 * 開くか（anchor）と高さが分からず外すので、描画後の実寸を測ってから寄せる。
 */
function nudgeIntoView(pop: maplibregl.Popup): void {
  requestAnimationFrame(() => {
    const el = pop.getElement()
    if (!el) return
    const r = el.getBoundingClientRect()
    const c = map.getContainer().getBoundingClientRect()
    // デスクトップは左のパネル、モバイルは下のボトムシートを避ける
    const left = !isMobile && !panel.classList.contains('collapsed') ? panel.offsetWidth + 8 : 8
    const bottom = isMobile ? panel.offsetHeight + 8 : 8

    let dx = 0
    if (r.left < c.left + left) dx = r.left - (c.left + left)
    else if (r.right > c.right - 8) dx = r.right - (c.right - 8)

    let dy = 0
    if (r.top < c.top + 8) dy = r.top - (c.top + 8)
    else if (r.bottom > c.bottom - bottom) dy = r.bottom - (c.bottom - bottom)

    if (dx !== 0 || dy !== 0) map.panBy([dx, dy], { duration: 200 })
  })
}

let popup: maplibregl.Popup | null = null
map.on('click', (e) => {
  if (!map.getLayer(LYR)) return
  // 点は小さいので、クリック位置の周囲数pxを拾う
  const pad = 6
  const box: [maplibregl.PointLike, maplibregl.PointLike] = [
    [e.point.x - pad, e.point.y - pad],
    [e.point.x + pad, e.point.y + pad],
  ]
  const feats = map.queryRenderedFeatures(box, { layers: [LYR] })
  if (!feats.length) {
    setHighlight(null)
    return
  }
  const f = feats[0]
  if (popup) {
    const old = popup
    popup = null
    old.remove()
  }
  setHighlight(f)

  const p = f.properties as Record<string, unknown>
  // 座標は属性の LAT/LON（原本の値）を使う。タイルのジオメトリは z9 で約19mに
  // 量子化されているため、現地確認のリンクには向かない。
  const geom = f.geometry as GeoJSON.Point
  const lat = typeof p.LAT === 'number' ? p.LAT : geom.coordinates[1]
  const lon = typeof p.LON === 'number' ? p.LON : geom.coordinates[0]

  const pop = new maplibregl.Popup({ closeButton: true, maxWidth: '340px', className: 'well-popup' })
    .setLngLat([lon, lat])
    .setHTML(popupHtml(p, lat, lon))
    .addTo(map)
  nudgeIntoView(pop)
  pop.on('close', () => {
    if (popup === pop) {
      popup = null
      setHighlight(null)
    }
  })
  popup = pop
})

// ---- 初期化 ----

const buildEl = document.getElementById('build-ver')
if (buildEl) buildEl.textContent = `build: ${__BUILD_TIME__}`

const opacityRange = document.getElementById('opacity-range') as HTMLInputElement
const opacityVal = document.getElementById('opacity-val') as HTMLElement
opacityRange.value = String(opacity)
opacityVal.textContent = `${Math.round(opacity * 100)}%`
opacityRange.addEventListener('input', () => {
  opacity = Number(opacityRange.value)
  opacityVal.textContent = `${Math.round(opacity * 100)}%`
  repaint()
})

renderThemeBtn()
buildThemeButtons()
buildPosqFilters()
setActiveTheme(themeKey)
syncFilterUi()
renderCount()
renderZoomNote()
if (isMobile) panel.classList.add('collapsed')
renderCollapseBtn()

map.on('load', addDataLayers)
map.on('zoomend', renderZoomNote)

// WebGL コンテキスト消失からの復帰。iOS Safari 等ではメモリ逼迫時に GL コンテキストが
// 失われ、データ層がまるごと消えて戻らないことがある。
const canvas = map.getCanvas()
canvas.addEventListener('webglcontextlost', (e) => e.preventDefault(), false)
canvas.addEventListener(
  'webglcontextrestored',
  () => {
    if (map.isStyleLoaded()) addDataLayers()
    else map.once('idle', addDataLayers)
  },
  false,
)

// デバッグ/外部連携用
;(window as unknown as { __map: maplibregl.Map }).__map = map

// PWA: Service Worker 登録（本番のみ。dev では HMR を妨げないよう無効）
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => {})
  })
  let refreshing = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (refreshing) return
    refreshing = true
    window.location.reload()
  })
}
