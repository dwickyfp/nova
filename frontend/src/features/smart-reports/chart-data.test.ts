import { describe, expect, it } from 'vitest'
import fixture from './fixtures/basic.json'
import { chartData } from './chart-data'
import { reportSpecSchema, type WidgetResult } from './contracts'

const widget = reportSpecSchema.parse(fixture.spec).pages[0].sections[0].widgets[1]
const result = { columns: ['city', 'revenue', 'comparison_period'], rows: [['Jakarta', '90', 'current_period'], ['Jakarta', '80', 'previous_period'], ['Bandung', null, 'current_period']] } as WidgetResult

describe('Report chart grouping', () => {
  it('keeps comparison periods separate and missing values null', () => {
    const visual = { ...widget, encoding: { ...widget.encoding, x: 'city', y: ['revenue'] }, query: { ...widget.query!, dimensions: ['city'], time: { dimension: 'date', grain: null, range: 'this_month', compare: 'previous_period' } } }
    const data = chartData(visual, result)
    expect(data.categories).toEqual(['Jakarta', 'Bandung'])
    expect(data.series).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: 'current_period · revenue', values: [90, null] }),
      expect.objectContaining({ name: 'previous_period · revenue', values: [80, null] }),
    ]))
    expect(result.rows[1][1]).toBe('80')
  })
})
