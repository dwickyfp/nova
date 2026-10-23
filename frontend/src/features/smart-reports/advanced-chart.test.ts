import { describe, expect, it } from 'vitest'
import fixture from './fixtures/basic.json'
import { advancedChart } from './advanced-chart'
import { compareDecimal } from './decimal'
import { reportSpecSchema, type WidgetResult } from './contracts'

const widget = reportSpecSchema.parse(fixture.spec).pages[0].sections[0].widgets[1]
describe('Report advanced visual values', () => {
  it('distinguishes decimal thresholds above the integer precision boundary', () => {
    expect(compareDecimal('9007199254740993.12', '9007199254740993.11')).toBe(1)
    expect(compareDecimal('-0.001', '0')).toBe(-1)
    expect(compareDecimal('1.2300', '123e-2')).toBe(0)
    expect(compareDecimal(null, '0')).toBeNull()
    expect(compareDecimal('Infinity', '0')).toBeNull()
    expect(compareDecimal('1e99999', '0')).toBeNull()
  })
  it('keeps heatmap groups distinct and excludes null values without turning them into zero', () => {
    const result = { columns: ['city', 'product', 'revenue'], rows: [['Jakarta', 'A', '12.5'], ['Bandung', 'A', null], ['Bandung', 'B', '-3']] } as WidgetResult
    const options = advancedChart({ ...widget, kind: 'heatmap', encoding: { ...widget.encoding, x: 'city', series: 'product', value: 'revenue' } }, result, ['red', 'green'], 'black')
    expect(options.series?.[0].data).toEqual([[0, 0, 12.5], [1, 1, -3]])
    expect(result.rows[1][2]).toBeNull()
  })
  it('places negative waterfall contributions below their prior cumulative value', () => {
    const result = { columns: ['step', 'amount'], rows: [['Start', '100'], ['Cost', '-30'], ['Other', '5']] } as WidgetResult
    const options = advancedChart({ ...widget, kind: 'waterfall', encoding: { ...widget.encoding, x: 'step', value: 'amount' } }, result, ['red', 'green'], 'black')
    expect(options.series?.[0].data).toEqual([['Start', 0, 100], ['Cost', 100, 70], ['Other', 70, 75]])
  })
})
