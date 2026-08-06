import { defineConfig, type Plugin } from 'vite'
import { createReadStream, existsSync, statSync } from 'node:fs'
import { join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const rootDir = fileURLToPath(new URL('.', import.meta.url))
// PMTiles は scripts/14_build_pmtiles.py がリポジトリ直下 output/ に出す。
const PMTILES_DIR = resolve(rootDir, '..', 'output')

/**
 * 開発サーバーで output/*.pmtiles を Range(206) 対応で配信するミドルウェア。
 * pmtiles.js は HTTP Byte Serving を要求するため、これがないとタイルを読めない。
 * 本番（GitHub Pages）では PMTiles を同梱し、静的ホストが Range を処理する。
 */
function pmtilesDevServer(): Plugin {
  return {
    name: 'pmtiles-dev-server',
    configureServer(server) {
      server.middlewares.use('/pmtiles', (req, res, next) => {
        try {
          const urlPath = decodeURIComponent((req.url ?? '').split('?')[0])
          const rel = normalize(urlPath).replace(/^([/\\]|\.\.[/\\])+/, '')
          const file = join(PMTILES_DIR, rel)
          if (!file.startsWith(PMTILES_DIR) || !existsSync(file)) {
            res.statusCode = 404
            res.end('Not found')
            return
          }
          const size = statSync(file).size
          res.setHeader('Accept-Ranges', 'bytes')
          res.setHeader('Access-Control-Allow-Origin', '*')
          res.setHeader('Content-Type', 'application/octet-stream')

          const range = req.headers.range
          const m = range ? /bytes=(\d*)-(\d*)/.exec(range) : null
          if (m) {
            const start = m[1] ? parseInt(m[1], 10) : 0
            let end = m[2] ? parseInt(m[2], 10) : size - 1
            end = Math.min(end, size - 1)
            if (start > end || start >= size) {
              res.statusCode = 416
              res.setHeader('Content-Range', `bytes */${size}`)
              res.end()
              return
            }
            res.statusCode = 206
            res.setHeader('Content-Range', `bytes ${start}-${end}/${size}`)
            res.setHeader('Content-Length', String(end - start + 1))
            createReadStream(file, { start, end }).pipe(res)
            return
          }

          res.statusCode = 200
          res.setHeader('Content-Length', String(size))
          createReadStream(file).pipe(res)
        } catch (err) {
          next(err)
        }
      })
    },
  }
}

// WSL2 から Windows 側（/mnt/c/...）のファイルを見ていると inotify が届かず、
// 保存しても HMR が走らない（vite が変換結果を握ったままになる）。その環境だけ
// ポーリング監視に切り替える。Linux/macOS ネイティブでは既定のままにする。
const onWindowsMount = rootDir.startsWith('/mnt/')

export default defineConfig({
  base: './',
  plugins: [pmtilesDevServer()],
  // layers.ts が ../../data/viewer_styles.json（viewer/ の外）を import するため、
  // dev サーバーにもリポジトリ直下の読み取りを許可する。
  server: {
    port: 8000,
    fs: { allow: ['..'] },
    watch: onWindowsMount ? { usePolling: true, interval: 400 } : undefined,
  },
  define: {
    __BUILD_TIME__: JSON.stringify(new Date().toISOString().replace('T', ' ').slice(0, 16) + ' UTC'),
  },
})
