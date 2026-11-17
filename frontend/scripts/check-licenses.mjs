// Fail when the installed npm tree or the frontend source needs a non-open license.
//
// Two checks, run against the tree `pnpm install --frozen-lockfile` produced:
// - every installed package (direct and transitive, development tools
//   included) declares a license on the allowlist below, or is a named,
//   reviewed exception;
// - no dependency is a known commercial or open-core package, and no frontend
//   source calls a license-key or trial API, so no required feature depends on
//   an enterprise key, a trial or a source-available license.
//
// Usage: pnpm check:licenses [--report path.json]. Exit status 1 on any finding.

import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, relative } from 'node:path'

const root = new URL('..', import.meta.url).pathname
const store = join(root, 'node_modules', '.pnpm')

export const ALLOWED = new Set([
  '0BSD', 'Apache-2.0', 'BlueOak-1.0.0', 'BSD-2-Clause', 'BSD-3-Clause', 'CC0-1.0', 'ISC',
  'MIT', 'MPL-2.0', 'Python-2.0', 'Unlicense', 'Zlib',
])
// A license accepted only for the named packages, with the review that accepted it.
export const REVIEWED = {
  'CC-BY-4.0': [['caniuse-lite'], 'Browser support data used at build time; attribution kept in the package.'],
}
const DENIED = /BUSL|Business Source|SSPL|Elastic|Commons Clause|PolyForm|Proprietary|Commercial|UNLICENSED|SEE LICENSE|AGPL|(?<!L)GPL|CC-BY-NC/i
export const COMMERCIAL = /^(ag-grid-enterprise|ag-charts-enterprise|@ag-grid-enterprise\/|@univerjs-pro\/|highcharts|@highcharts\/|@amcharts\/|amcharts|handsontable|@handsontable\/|@mui\/x-[a-z-]+-(pro|premium)$|@syncfusion\/|devextreme|@devexpress\/|@progress\/kendo|fusioncharts|anychart|@tiptap-pro\/|@fullcalendar\/premium)/
export const LICENSE_KEY_API = /setLicenseKey|LicenseManager|registerLicense|licenseKey\s*[:=]/

export function accepted(expression, name) {
  const tokens = expression.match(/\(|\)|[A-Za-z0-9.+-]+/g) ?? []
  let position = 0
  const term = () => {
    const token = tokens[position++]
    let value
    if (token === '(') {
      value = either()
      if (tokens[position++] !== ')') throw new Error('unbalanced')
    } else {
      value = ALLOWED.has(token) || (REVIEWED[token]?.[0].includes(name) ?? false)
    }
    if (tokens[position] === 'WITH') {
      value = value && tokens[position + 1] === 'LLVM-exception'
      position += 2
    }
    return value
  }
  const both = () => {
    let value = term()
    while (tokens[position] === 'AND') { position++; value = term() && value }
    return value
  }
  const either = () => {
    let value = both()
    while (tokens[position] === 'OR') { position++; value = both() || value }
    return value
  }
  try {
    return tokens.length > 0 && either() && position === tokens.length
  } catch {
    return false
  }
}

export function declared(pkg) {
  let license = pkg.license
  if (license && typeof license === 'object') license = license.type
  if (!license && Array.isArray(pkg.licenses)) {
    license = pkg.licenses.map((item) => (typeof item === 'object' ? item.type : item)).join(' OR ')
  }
  return typeof license === 'string' && license.trim() ? license.trim() : null
}

function installed() {
  const packages = new Map()
  for (const entry of readdirSync(store)) {
    const modules = join(store, entry, 'node_modules')
    if (!existsSync(modules)) continue
    for (const name of readdirSync(modules)) {
      const names = name.startsWith('@') ? readdirSync(join(modules, name)).map((item) => `${name}/${item}`) : [name]
      for (const full of names) {
        const file = join(modules, full, 'package.json')
        // Links to other store entries are visited from their own entry.
        if (!existsSync(file) || !entry.startsWith(full.replace('/', '+'))) continue
        const pkg = JSON.parse(readFileSync(file, 'utf8'))
        if (pkg.name === full) packages.set(`${pkg.name}@${pkg.version}`, pkg)
      }
    }
  }
  return packages
}

function sources(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((item) => {
    const path = join(directory, item.name)
    if (item.isDirectory()) return item.name === '__screenshots__' ? [] : sources(path)
    return /\.(ts|tsx|js|jsx|mjs)$/.test(item.name) && !/\.test\./.test(item.name) ? [path] : []
  })
}

function main() {
  const reportAt = process.argv.indexOf('--report')
  const findings = []
  const licenses = {}
  const exceptions = {}
  const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
  const direct = Object.keys({ ...manifest.dependencies, ...manifest.devDependencies, ...manifest.optionalDependencies })
  for (const name of direct) if (COMMERCIAL.test(name)) findings.push(`${name}: commercial or open-core package`)
  for (const [label, pkg] of installed()) {
    const expression = declared(pkg)
    if (COMMERCIAL.test(pkg.name)) findings.push(`${label}: commercial or open-core package`)
    if (!expression) { findings.push(`${label}: no license metadata`); continue }
    licenses[label] = expression
    const reviewed = Object.entries(REVIEWED).find(([id, [names]]) => expression.includes(id) && names.includes(pkg.name))
    if (reviewed) exceptions[label] = `${reviewed[0]}: ${reviewed[1][1]}`
    if (DENIED.test(expression) && !reviewed) findings.push(`${label}: prohibited license ${expression}`)
    else if (!accepted(expression, pkg.name)) findings.push(`${label}: license ${expression} is not on the allowlist`)
  }
  for (const file of sources(join(root, 'src'))) {
    readFileSync(file, 'utf8').split('\n').forEach((line, index) => {
      if (LICENSE_KEY_API.test(line)) findings.push(`${relative(root, file)}:${index + 1}: license-key API`)
    })
  }
  const count = Object.keys(licenses).length
  const report = {
    ecosystem: 'npm', packages: count, findings, reviewed_exceptions: exceptions,
    licenses: Object.fromEntries(Object.entries(licenses).sort()), result: findings.length ? 'FAIL' : 'PASS',
  }
  if (reportAt > 0) writeFileSync(process.argv[reportAt + 1], `${JSON.stringify(report, null, 2)}\n`)
  for (const finding of findings) console.log(`FAIL ${finding}`)
  console.log(`${report.result}: ${count} npm packages checked, ${Object.keys(exceptions).length} reviewed exceptions, ${findings.length} findings`)
  process.exitCode = findings.length ? 1 : 0
}

if (process.argv[1] && new URL(import.meta.url).pathname === process.argv[1]) main()
