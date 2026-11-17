import { describe, expect, it } from 'vitest'
import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { printImage } from '../charts/print-image'
import { reportHtml, resultTable } from './export'
import '@/styles/index.css'

// Release QA NDV-STORE-007 (docs/reports/native-dataviz/QA_MATRIX.csv).

const DATASET = 'aaaaaaaa-0000-4000-8000-0000000000c1'
const chart = {
  schema_version: 2, chart_id: 'cccccccc-0000-4000-8000-0000000000c1', title: 'Revenue by region', dataset_id: DATASET,
  dataset_revision: 1, visual: 'bar',
  encoding: { kind: 'cartesian', dimensions: [{ field_id: 'region' }], measures: [{ field_id: 'revenue', aggregation: 'sum' }] },
} as unknown as ChartDefinition
const result: DatasetQueryResult = {
  dataset_id: DATASET, revision: 1, truncated: false,
  columns: [
    { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
    { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' },
  ] as DatasetQueryResult['columns'],
  rows: [[{ type: 'string', value: 'Jakarta' }, { type: 'decimal', value: '400' }], [{ type: 'string', value: 'Bandung' }, { type: 'decimal', value: '600' }]],
}

describe('NDV-STORE-007 offline report', () => {
  it('holds static values and inline images only, with no Nova endpoint, script or token', async () => {
    const image = await printImage(chart, result, 480, 280)
    expect(image).toMatch(/^data:image\/png;base64,[A-Za-z0-9+/=]+$/)
    const html = reportHtml({
      title: 'Sales overview', subtitle: 'Overview · Data as of 16 Nov 2026', filters: ['Region: Jakarta'],
      items: [
        { kind: 'kpi', title: 'Units sold', value: '15,257' },
        { kind: 'image', title: chart.title, src: image, width: 480 },
        { kind: 'table', title: 'Rows', table: resultTable(result) },
      ],
    })
    const doc = new DOMParser().parseFromString(html, 'text/html')
    // Every image is embedded; nothing is fetched when the report is opened.
    const images = [...doc.querySelectorAll('img')]
    expect(images).toHaveLength(1)
    expect(images.every(img => img.getAttribute('src')!.startsWith('data:image/png;base64,'))).toBe(true)
    expect(doc.querySelectorAll('script, link, iframe, object, embed, form, base, meta[http-equiv]')).toHaveLength(0)
    expect([...doc.querySelectorAll('[href], [action], [srcset]')]).toHaveLength(0)
    const text = html.replace(image, '')
    expect(text).not.toMatch(/https?:|\/\/[a-z]/i)
    expect(text).not.toMatch(/\/api\/|\/viz\//)
    expect(text).not.toMatch(/bearer|authorization|token|cookie/i)
    expect(text).not.toMatch(/url\(/i)
    // The governed values are in the page itself.
    expect(doc.querySelector('.kpi')?.textContent).toBe('15,257')
    expect([...doc.querySelectorAll('tbody td')].map(cell => cell.textContent)).toEqual(['Jakarta', '400', 'Bandung', '600'])
  })
})
