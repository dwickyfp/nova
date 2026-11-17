// NDV-OSS-001/002: the npm license gate refuses what it must.
import { expect, it } from 'vitest'
import { accepted, COMMERCIAL, declared, LICENSE_KEY_API } from '../scripts/check-licenses.mjs'

it.each([
  ['MIT', 'any', true],
  ['(MPL-2.0 OR Apache-2.0)', 'any', true],
  ['MIT AND ISC', 'any', true],
  ['MIT AND GPL-3.0-only', 'any', false],
  ['BUSL-1.1', 'any', false],
  ['SSPL-1.0', 'any', false],
  ['Elastic-2.0', 'any', false],
  ['SEE LICENSE IN LICENSE.md', 'any', false],
  ['UNLICENSED', 'any', false],
  ['CC-BY-4.0', 'caniuse-lite', true],
  ['CC-BY-4.0', 'another-package', false],
  ['(MIT', 'any', false],
])('%s for %s is %s', (expression, name, expected) => {
  expect(accepted(expression, name)).toBe(expected)
})

it('reads license, license objects and legacy licenses arrays', () => {
  expect(declared({ license: 'MIT' })).toBe('MIT')
  expect(declared({ license: { type: 'ISC' } })).toBe('ISC')
  expect(declared({ licenses: [{ type: 'MIT' }, { type: 'Apache-2.0' }] })).toBe('MIT OR Apache-2.0')
  expect(declared({})).toBeNull()
})

it('recognises commercial packages and license-key calls', () => {
  for (const name of ['ag-grid-enterprise', '@univerjs-pro/sheets', 'highcharts', '@mui/x-data-grid-pro', '@amcharts/amcharts5']) {
    expect(COMMERCIAL.test(name)).toBe(true)
  }
  for (const name of ['echarts', 'gridstack', '@tanstack/react-table', '@mui/x-data-grid']) {
    expect(COMMERCIAL.test(name)).toBe(false)
  }
  expect(LICENSE_KEY_API.test('LicenseManager.setLicenseKey(key)')).toBe(true)
  expect(LICENSE_KEY_API.test('const options = { licenseKey: KEY }')).toBe(true)
  expect(LICENSE_KEY_API.test('const licenses = audit()')).toBe(false)
})
