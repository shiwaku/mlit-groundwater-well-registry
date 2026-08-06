// キャッシュしない Service Worker。常に最新をネットワークから取得する。
// 「キャッシュ優先」の SW は古い画面を固定してしまうため、有効化時に既存キャッシュを消す。
self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    (async () => {
      for (const k of await caches.keys()) await caches.delete(k)
      await self.clients.claim()
    })(),
  )
})

// HTML（ナビゲーション）だけネットワーク優先で、HTTPキャッシュもバイパスして取得する。
// GitHub Pages は index.html を Cache-Control: max-age=600 で返すため、通常リロードでは
// 最大10分間 古い index.html（＝古いJS/CSSハッシュを参照）が使われ、修正が反映されない。
// ハッシュ付きアセットは不変なので介入しない。
self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.mode === 'navigate') {
    event.respondWith(fetch(req, { cache: 'no-store' }).catch(() => fetch(req)))
  }
})
