// Bundle budget: fails the build check when shipped JavaScript grows past an
// approved size. Sizes are gzipped bytes, as browsers download them. Raise a
// budget only on purpose, in the same change that explains the growth.
import { existsSync, readFileSync, readdirSync } from 'node:fs'
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

// Notices: the bundle ships every bundled dependency's license, the NOTICE of
// each one that has a NOTICE file, and a license beside every font family.
const notices = []
const thirdParty = join(dist, 'licenses', 'third-party.md')
const listing = existsSync(thirdParty) ? readFileSync(thirdParty, 'utf8') : ''
const bundled = [...listing.matchAll(/^## (\S+) - (\S+) \(/gm)]
if (bundled.length < 50) notices.push(`${thirdParty} lists ${bundled.length} bundled packages`)
const store = join('node_modules', '.pnpm')
for (const [, name, version] of bundled) {
  const prefix = `${name.replace('/', '+')}@${version}`
  const entries = readdirSync(store).filter(item => item === prefix || item.startsWith(prefix + '_'))
  const shipsNotice = entries.some(item => {
    const directory = join(store, item, 'node_modules', name)
    return existsSync(directory) && readdirSync(directory).some(file => /^NOTICE(\.(md|txt))?$/i.test(file))
  })
  if (shipsNotice && !listing.includes(`### ${name} - ${version}\n`)) notices.push(`NOTICE of ${name}@${version} is missing`)
}
const fonts = readdirSync(join(dist, 'fonts'))
const flat = value => value.toLowerCase().replace(/[^a-z]/g, '')
for (const family of new Set(fonts.filter(file => file.endsWith('.woff2')).map(file => file.split(/-latin/)[0]))) {
  if (!fonts.some(file => file.startsWith('LICENSE-') && flat(file.slice(8)) === flat(family))) notices.push(`font ${family} has no license file`)
}
// ECharts is used through its modular core; the full entry would bundle every chart.
const walk = directory => readdirSync(directory, { withFileTypes: true }).flatMap(item =>
  item.isDirectory() ? walk(join(directory, item.name)) : /\.(ts|tsx)$/.test(item.name) ? [join(directory, item.name)] : [])
for (const file of walk('src')) {
  const text = readFileSync(file, 'utf8')
  if (/from ['"]echarts['"]|import\(['"]echarts['"]\)/.test(text)) notices.push(`${file} imports the full ECharts entry`)
  // ECharts 6 ignores grid.containLabel unless LegacyGridContainLabel is registered.
  if (/containLabel\s*:/.test(text) && !text.includes('LegacyGridContainLabel')) notices.push(`${file} uses grid.containLabel; use outerBoundsMode/outerBoundsContain`)
}
for (const problem of notices) console.log(`MISSING ${problem}`)
console.log(`${notices.length ? 'MISSING' : 'ok  '} notices      ${bundled.length} packages, ${fonts.filter(file => file.startsWith('LICENSE-')).length} font licenses`)
failed ||= notices.length > 0
process.exit(failed ? 1 : 0)
