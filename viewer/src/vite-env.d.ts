/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PMTILES_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

/** vite.config.ts の define で埋め込まれるビルド時刻。 */
declare const __BUILD_TIME__: string

/**
 * `?url` 付きの GeoJSON import。ビルド時に dist/assets/ へハッシュ付きで置かれ、
 * URL だけが JS に入る（＝バンドルに 260KB の本文を抱えず、MapLibre に取りに行かせる）。
 */
declare module '*.geojson?url' {
  const src: string
  export default src
}
