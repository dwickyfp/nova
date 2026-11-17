import { describe, expect, it } from 'vitest'
import type { DatasetQueryResult } from '../contracts'
import { fileName, hasImage, reportHtml, resultTable, sheetNames, toCsv, toXlsx } from './export'

const result = (rows: DatasetQueryResult['rows']): DatasetQueryResult => ({
  dataset_id: 'd', revision: 1, truncated: false,
  columns: [
    { key: 'd0', name: 'orders.region', logical_type: 'string', role: 'dimension', field_id: 'region' },
    { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' },
  ] as DatasetQueryResult['columns'],
  rows,
})

describe('chart downloads', () => {
  it('keeps numbers as numbers, labels columns plainly and never loses digits', () => {
    const table = resultTable(result([
      [{ type: 'string', value: 'Jakarta' }, { type: 'decimal', value: '979755000.00' }],
      [{ type: 'string', value: 'Big' }, { type: 'decimal', value: '123456789012345678901.5' }],
      [null, null],
    ]))
    expect(table.headers).toEqual(['Region', 'Sum of revenue'])
    expect(table.rows).toEqual([['Jakarta', 979755000], ['Big', '123456789012345678901.5'], [null, null]])
  })

  it('writes RFC 4180 CSV that spreadsheet apps read as UTF-8', async () => {
    const csv = toCsv({ headers: ['Name', 'Note'], rows: [['A, B', 'said "hi"'], ['Line\nbreak', null], [' padded', 3]] })
    const bytes = new Uint8Array(await csv.arrayBuffer())
    expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf])
    expect(await csv.text()).toBe('Name,Note\r\n"A, B","said ""hi"""\r\n"Line\nbreak",\r\n" padded",3\r\n')
  })

  it('keeps formula-shaped text from running in a spreadsheet', async () => {
    const csv = toCsv({ headers: ['Value'], rows: [['=HYPERLINK("http://x")'], ['+1'], ['@SUM(A1)'], [-5], ['-x']] })
    expect(await csv.text()).toBe('Value\r\n"\'=HYPERLINK(""http://x"")"\r\n\'+1\r\n\'@SUM(A1)\r\n-5\r\n\'-x\r\n')
  })

  it('stores formula-shaped text in Excel as text, not as a formula', async () => {
    const XLSX = await import('xlsx')
    const book = XLSX.read(await (await toXlsx([{ title: 'S', table: { headers: ['V'], rows: [['=1+1'], [2]] } }])).arrayBuffer())
    const sheet = book.Sheets.S
    expect(sheet.A2.t).toBe('s')
    expect(sheet.A2.f).toBeUndefined()
    expect(sheet.A3).toMatchObject({ t: 'n', v: 2 })
  })

  it('makes valid, unique Excel sheet names', () => {
    const long = 'Berapa total sell-out unit per bulan dari Agustus?'
    const names = sheetNames([long, long, 'Revenue: by/region?', '', 'revenue  by region'])
    expect(names[0]).toBe('Berapa total sell-out unit per')
    expect(names[1]).toBe('Berapa total sell-out unit (2)')
    expect(names[2]).toBe('Revenue by region')
    expect(names[3]).toBe('Chart')
    expect(names[4]).toBe('revenue by region (2)')
    expect(names.every(name => name.length <= 31 && !/[[\]:*?/\\]/.test(name) && name === name.trim())).toBe(true)
  })

  it('names files safely', () => {
    expect(fileName('Revenue / region: 2026?', 'csv')).toBe('Revenue region 2026.csv')
    expect(fileName('   ', 'png')).toBe('chart.png')
    expect(fileName('Penjualan Café', 'xlsx')).toBe('Penjualan Café.xlsx')
  })

  it('offers images only for drawn charts', () => {
    expect(hasImage({ visual: 'bar' })).toBe(true)
    expect(hasImage({ visual: 'kpi' })).toBe(false)
    expect(hasImage({ visual: 'table' })).toBe(false)
  })

  it('escapes every text in the printable report', () => {
    const html = reportHtml({
      title: '<script>alert(1)</script>', subtitle: 'Overview · today',
      items: [
        { kind: 'text', title: 'Note', text: '<img src=x onerror=alert(1)>' },
        { kind: 'kpi', title: 'Units "sold"', value: '15,257' },
        { kind: 'table', title: 'Rows', table: { headers: ['A&B'], rows: [[1234.5], ['<b>']] } },
      ],
    })
    expect(html).not.toContain('<script>alert')
    expect(html).not.toContain('<img src=x')
    expect(html).toContain('&lt;script&gt;')
    expect(html).toContain('Units &quot;sold&quot;')
    expect(html).toContain('A&amp;B')
    expect(html).toContain('<td>&lt;b&gt;</td>')
    expect(html).toMatch(/<td class="num">1,234.5<\/td>/)
  })

  it('starts every dashboard page on a new sheet and lists the filters in effect', () => {
    const html = reportHtml({
      title: 'Sales', subtitle: '2 pages · Data as of today', filters: ['Region: Jakarta', 'Selected <Online>'],
      items: [
        { kind: 'page', title: 'Overview' }, { kind: 'kpi', title: 'Units', value: '1' },
        { kind: 'page', title: 'Detail' }, { kind: 'text', title: '', text: 'Notes' },
      ],
    })
    expect(html).toContain('<p class="meta">Filters: Region: Jakarta · Selected &lt;Online&gt;</p>')
    expect(html).toContain('<h2 class="page">Overview</h2>')
    expect(html).toContain('<h2 class="page break">Detail</h2>')
    expect(html.indexOf('Overview')).toBeLessThan(html.indexOf('Detail'))
  })
})
