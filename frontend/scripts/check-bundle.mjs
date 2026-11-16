// Bundle budget: fails the build check when shipped JavaScript grows past an
// approved size. Sizes are gzipped bytes, as browsers download them. Raise a
// budget only on purpose, in the same change that explains the growth.
import { readFileSync, readdirSync } from 'node:fs'
import { gzipSync } from 'node:zlib'
import { join } from 'node:path'

const BUDGETS = {
  total: 4_000_000,        // every JavaScript file the app can load (3.68 MB on 2026-11-16)
  largestChunk: 175_000,   // any single chunk (158.6 kB vendor chunk on 2026-11-16)
  firstLoad: 690_000,      // scripts index.html loads before any route (626.5 kB on 2026-11-16)
}

const dist = 'dist'
const assets = join(dist, 'assets')
const size = file => gzipSync(readFileSync(file)).length
const files = readdirSync(assets).filter(name => name.endsWith('.js'))
const sizes = new Map(files.map(name => [name, size(join(assets, name))]))
const html = readFileSync(join(dist, 'index.html'), 'utf8')
const entry = [...html.matchAll(/(?:src|href)="\/?assets\/([^"]+\.js)"/g)].map(match => match[1])
const measured = {
  total: [...sizes.values()].reduce((sum, value) => sum + value, 0),
  largestChunk: Math.max(...sizes.values()),
  firstLoad: entry.reduce((sum, name) => sum + (sizes.get(name) ?? 0), 0),
}
let failed = false
for (const [name, budget] of Object.entries(BUDGETS)) {
  const value = measured[name]
  const ok = value <= budget
  failed ||= !ok
  console.log(`${ok ? 'ok  ' : 'OVER'} ${name.padEnd(13)} ${(value / 1000).toFixed(1).padStart(8)} kB of ${(budget / 1000).toFixed(1)} kB`)
}
if (!entry.length) {
  console.log('OVER firstLoad: no entry script found in dist/index.html')
  failed = true
}
process.exit(failed ? 1 : 0)
