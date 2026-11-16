import { describe, expect, it } from 'vitest'
import type { ChartDefinition, DatasetField, DatasetQueryResult } from '../contracts'
import { fromChart, initialState, missing, switchVisual, toChart } from './builder-model'
import { chartQueryShape } from './chart-query'
import { hasImage } from '../dashboard/export'
import { mapFeatures, mapModel } from './map-points'

const field = (field_id: string, logical_type: DatasetField['logical_type'], name = field_id) =>
  ({ field_id, name, source_column: name, logical_type, physical_type: logical_type, semantic_role: 'dimension', nullable: true }) as DatasetField
const fields = [field('city', 'string'), field('lat', 'float', 'latitude'), field('lng', 'float', 'longitude'), field('pop', 'integer', 'population')]

const map = (group: boolean): ChartDefinition => ({
  schema_version: 2, chart_id: 'c', title: 'Cities', dataset_id: 'd', dataset_revision: 1, visual: 'map',
  encoding: { kind: 'map', latitude: 'lat', longitude: 'lng', value: { field_id: 'pop', aggregation: 'sum' },
    ...(group ? { group: { field_id: 'city' } } : {}) },
}) as unknown as ChartDefinition

const column = (key: string, name: string, logical_type: string, role: 'dimension' | 'measure') =>
  ({ key, name, logical_type, role, field_id: name }) as DatasetQueryResult['columns'][number]
const f = (value: number) => ({ type: 'float' as const, value })
const s = (value: string) => ({ type: 'string' as const, value })

describe('map points', () => {
  const columns = [column('d0', 'lat', 'float', 'dimension'), column('d1', 'lng', 'float', 'dimension'),
    column('d2', 'city', 'string', 'dimension'), column('m0', 'pop', 'integer', 'measure')]
  const result = (rows: DatasetQueryResult['rows']): DatasetQueryResult =>
    ({ dataset_id: 'd', revision: 1, columns, rows, truncated: false })

  it('places each row at its coordinates, sized by the square root of its share of the largest value', () => {
    const model = mapModel(map(true), result([
      [f(-6.2), f(106.8), s('Jakarta'), f(400)],
      [f(-6.9), f(107.6), s('Bandung'), f(100)],
    ]))
    expect(model.points.map(point => [point.group, point.weight])).toEqual([['Jakarta', 1], ['Bandung', 0.5]])
    expect(model.groups).toEqual(['Jakarta', 'Bandung'])
    expect(model.bounds).toEqual([[106.8, -6.9], [107.6, -6.2]])
    expect(mapFeatures(model).features[1].geometry.coordinates).toEqual([107.6, -6.9])
  })

  it('draws plain dots when no value sizes them', () => {
    const counted = { ...map(true), encoding: { kind: 'map', latitude: 'lat', longitude: 'lng', group: { field_id: 'city' } } } as unknown as ChartDefinition
    const model = mapModel(counted, result([[f(1), f(2), s('A'), f(1)], [f(3), f(4), s('B'), f(1)]]))
    expect(model.sized).toBe(false)
    expect(model.points.map(point => point.weight)).toEqual([0, 0])
    expect(model.valueLabel).toBe('Rows')
  })

  it('counts rows with a missing or impossible coordinate instead of placing them', () => {
    const model = mapModel(map(true), result([
      [null, f(106.8), s('Nowhere'), f(1)],
      [f(95), f(10), s('Too far north'), f(1)],
      [f(1), f(2), s('Here'), f(3)],
    ]))
    expect(model.points.map(point => point.group)).toEqual(['Here'])
    expect(model.skipped).toBe(2)
  })

  it('reads the value after the coordinates when there is no group', () => {
    const plain = { ...result([[f(1), f(2), f(7)]]), columns: [columns[0], columns[1], columns[3]] }
    const model = mapModel(map(false), plain)
    expect(model.points[0]).toMatchObject({ value: 7, group: null, weight: 1 })
    expect(model.sized).toBe(true)
    expect(model.groups).toEqual([])
  })
})

describe('map builder', () => {
  it('picks latitude and longitude by name and needs both to save', () => {
    const state = switchVisual({ ...initialState({ chart_id: 'c', dataset_id: 'd', dataset_revision: 1, fields }), title: 'Cities' }, 'map', fields)
    expect([state.y, state.x]).toEqual(['lat', 'lng'])
    expect(missing(state)).toBeNull()
    expect(missing({ ...state, x: '' })).toBe('Choose the latitude and longitude fields')
  })

  it('round-trips a map with a value and a colour group', () => {
    const state = { ...initialState({ chart_id: 'c', dataset_id: 'd', dataset_revision: 1, fields }), title: 'Cities',
      visual: 'map' as const, y: 'lat', x: 'lng', color: { field_id: 'city', date_grain: null },
      measures: [{ field_id: 'pop', aggregation: 'sum' as const, series: 'bar' as const, axis: 'left' as const }] }
    const chart = toChart(state)
    expect(chart.encoding).toMatchObject({ kind: 'map', latitude: 'lat', longitude: 'lng', value: { field_id: 'pop' }, group: { field_id: 'city' } })
    expect((chartQueryShape(chart).dimensions ?? []).map(item => item.field_id)).toEqual(['lat', 'lng', 'city'])
    const back = fromChart(chart, fields)
    expect([back.y, back.x, back.color?.field_id, back.measures[0].field_id]).toEqual(['lat', 'lng', 'city', 'pop'])
  })

  it('counts rows when no value is chosen', () => {
    const state = { ...initialState({ chart_id: 'c', dataset_id: 'd', dataset_revision: 1, fields }), title: 'Cities',
      visual: 'map' as const, y: 'lat', x: 'lng', measures: [] }
    expect('value' in toChart(state).encoding).toBe(false)
    expect(chartQueryShape(toChart(state)).measures).toEqual([{ field_id: 'lat', aggregation: 'count' }])
  })

  it('exports a map image but never an image of a pivot', () => {
    expect(hasImage({ visual: 'map' })).toBe(true)
    expect(hasImage({ visual: 'pivot' })).toBe(false)
  })
})
