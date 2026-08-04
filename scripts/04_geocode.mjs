#!/usr/bin/env node
/**
 * track A（現行コードで市区町村名が確定している行）を
 * @geolonia/normalize-japanese-addresses で正規化・座標付与する。
 *
 * ADR は「町名・大字程度まで」の粒度なので level: 3（丁目・町字）を上限とする。
 * 地番・住居表示（level 8）まで解決させると不要な取得が大量に発生するため。
 *
 * 候補文字列は adr_norm.py が保守的な順に並べてあるので、先頭から試して
 * 最初に level>=3 に到達したものを採用する。到達しなければ市区町村代表点に落とす。
 *
 * 出力 geocode_result_a.jsonl は追記式。再実行すると既に解決済みの
 * addr_key はスキップするので、中断しても続きから再開できる。
 */
import fs from 'node:fs'
import readline from 'node:readline'
import { normalize, config } from '@geolonia/normalize-japanese-addresses'

const ROOT = new URL('..', import.meta.url).pathname
const IN = ROOT + 'work/geocode_input.jsonl'
const OUT = ROOT + 'work/geocode_result_a.jsonl'
const CONCURRENCY = 8

// 1,898 市区町村ぶんの町字データを保持できるようキャッシュを広げる
config.cacheSize = 4000

/** 既存の結果を読み込んで再開に備える */
function loadDone() {
  const done = new Set()
  if (!fs.existsSync(OUT)) return done
  for (const line of fs.readFileSync(OUT, 'utf-8').split('\n')) {
    if (!line.trim()) continue
    try { done.add(JSON.parse(line).addr_key) } catch { /* 壊れた行は無視 */ }
  }
  return done
}

async function readUnits() {
  const units = []
  const rl = readline.createInterface({ input: fs.createReadStream(IN, 'utf-8'), crlfDelay: Infinity })
  for await (const line of rl) {
    if (!line.trim()) continue
    const u = JSON.parse(line)
    if (u.track === 'A') units.push(u)
  }
  return units
}

/**
 * 1件を解決する。
 *
 * 注意: normalize() は level:3 を返しても point.level が 2（市区町村代表点）
 * のことがある（例「北海道空知郡南幌町中央」→ town は未確定で point は役場位置）。
 * 町字の精度が出たかどうかは r.level ではなく point.level で判定する必要がある。
 * そのため全候補を試して point.level が最も高いものを採る。
 */
function pack(u, cand, r, status) {
  return {
    addr_key: u.addr_key, status, used_cand: cand,
    norm_pref: r.pref ?? '', norm_city: r.city ?? '', norm_town: r.town ?? '',
    other: r.other ?? '', level: r.level,
    lat: r.point.lat, lng: r.point.lng, point_level: r.point.level,
  }
}

async function resolveUnit(u) {
  const base = u.pref + u.muni_full
  let best = null
  for (const cand of u.cands) {
    let r
    try {
      r = await normalize(base + cand, { level: 3 })
    } catch (e) {
      continue
    }
    if (!r.point) continue
    if (r.point.level >= 3) return pack(u, cand, r, 'town')
    if (!best || r.point.level > best.r.point.level) best = { cand, r }
  }
  if (best) return pack(u, best.cand, best.r, best.r.point.level >= 3 ? 'town' : 'city')

  // 候補が全滅（ADR 空を含む）ので市区町村代表点に落とす
  try {
    const r = await normalize(base, { level: 2 })
    if (r.point) return pack(u, '', r, r.point.level >= 3 ? 'town' : 'city')
  } catch (e) { /* 下の failed に落ちる */ }
  return { addr_key: u.addr_key, status: 'failed', used_cand: '', level: 0 }
}

async function main() {
  const done = loadDone()
  const all = await readUnits()
  const todo = all.filter((u) => !done.has(u.addr_key))
  console.log(`track A: 全 ${all.length.toLocaleString()} 件 / 未処理 ${todo.length.toLocaleString()} 件`)
  if (todo.length === 0) return

  const out = fs.createWriteStream(OUT, { flags: 'a' })
  const counts = { town: 0, city: 0, failed: 0 }
  let next = 0, finished = 0
  const t0 = Date.now()

  async function worker() {
    while (true) {
      const i = next++
      if (i >= todo.length) return
      const res = await resolveUnit(todo[i])
      counts[res.status]++
      out.write(JSON.stringify(res) + '\n')
      if (++finished % 2000 === 0) {
        const sec = (Date.now() - t0) / 1000
        const rate = finished / sec
        const eta = Math.round((todo.length - finished) / rate)
        console.log(`  ${finished.toLocaleString()}/${todo.length.toLocaleString()}  ` +
          `町字 ${counts.town.toLocaleString()} / 市区町村 ${counts.city.toLocaleString()} / 失敗 ${counts.failed.toLocaleString()}  ` +
          `(${rate.toFixed(1)}件/秒, 残り約${eta}秒)`)
      }
    }
  }

  await Promise.all(Array.from({ length: CONCURRENCY }, worker))
  await new Promise((r) => out.end(r))
  console.log(`\n完了: 町字 ${counts.town.toLocaleString()} / 市区町村 ${counts.city.toLocaleString()} / 失敗 ${counts.failed.toLocaleString()}`)
}

main().catch((e) => { console.error(e); process.exit(1) })
