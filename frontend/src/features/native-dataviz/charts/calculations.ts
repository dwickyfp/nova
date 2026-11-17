import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { cellNumber, humanize } from './chart-query'

type Calculation = NonNullable<ChartDefinition['table_calculations']>[number]
export type CalculationKind = Calculation['kind']
type Cell = DatasetQueryResult['rows'][number][number]

/** What each calculation is called where its values are shown. */
export const CALCULATION_LABELS: Record<CalculationKind, string> = {
  running_total: 'Running total',
  percent_of_total: 'Percent of total',
  difference: 'Difference from previous',
  percent_difference: 'Percent difference from previous',
  rank: 'Rank',
  dense_rank: 'Dense rank',
  moving_average: 'Moving average',
}

/** Calculations shown as percentages: 25 means 25 %. */
export const PERCENT_CALCULATIONS = new Set<CalculationKind>(['percent_of_total', 'percent_difference'])

function compare(a: Cell, b: Cell): number {
  if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1
  const x = cellNumber(a)
  const y = cellNumber(b)
  if (x !== null && y !== null) return x - y
  return String(a.value).localeCompare(String(b.value))
}

const value = (number: number | null): Cell =>
  number === null || !Number.isFinite(number) ? null : { type: 'float', value: Math.round(number * 1e9) / 1e9 }

/**
 * Apply a chart's table calculations to its governed result. They run after
 * aggregation, over the rows the chart shows: along the addressing fields in
 * their order, separately for each partition. Ties share a rank (1, 1, 3;
 * dense 1, 1, 2), ordered by value from high to low. A moving average uses the
 * previous rows in order, not calendar days. Nothing is invented: a missing
 * value stays missing unless the calculation counts it as zero.
 */
export function applyCalculations(chart: Pick<ChartDefinition, 'table_calculations'>, result: DatasetQueryResult): DatasetQueryResult {
  const calculations = chart.table_calculations ?? []
  if (!calculations.length || !result.rows.length) return result
  const rows = result.rows.map(row => [...row])
  const columns = result.columns.map(column => ({ ...column }))
  for (const calculation of calculations) {
    const target = columns.findIndex(column => column.role === 'measure' && column.field_id === calculation.measure.field_id
      && column.aggregation === calculation.measure.aggregation)
    if (target < 0) continue
    const at = (field: string) => columns.findIndex(column => column.role === 'dimension' && column.field_id === field)
    const order = calculation.order.map(item => ({ index: at(item.field_id), desc: item.direction === 'desc' }))
    const partition = (calculation.partition_by ?? []).map(at)
    if (order.some(item => item.index < 0) || partition.some(index => index < 0)) continue
    const groups = new Map<string, number[]>()
    rows.forEach((row, index) => {
      const key = JSON.stringify(partition.map(p => row[p]))
      groups.set(key, [...(groups.get(key) ?? []), index])
    })
    const raw = rows.map(row => {
      const number = cellNumber(row[target])
      return number === null && calculation.nulls === 'zero' ? 0 : number
    })
    for (const members of groups.values()) {
      const ordered = [...members].sort((a, b) => {
        for (const item of order) {
          const difference = compare(rows[a][item.index], rows[b][item.index])
          if (difference) return item.desc ? -difference : difference
        }
        return a - b
      })
      const present = ordered.map(index => raw[index]).filter((number): number is number => number !== null)
      const total = present.reduce((sum, number) => sum + number, 0)
      const ranked = [...new Set(present)].sort((a, b) => b - a)
      let running = 0
      ordered.forEach((index, position) => {
        const own = raw[index]
        const previous = position > 0 ? raw[ordered[position - 1]] : null
        let next: number | null = null
        switch (calculation.kind) {
          case 'running_total':
            if (own !== null) running += own
            next = own === null ? null : running
            break
          case 'percent_of_total':
            next = own === null || total === 0 ? null : own / total * 100
            break
          case 'difference':
            next = own === null || previous === null ? null : own - previous
            break
          case 'percent_difference':
            next = own === null || previous === null || previous === 0 ? null : (own - previous) / Math.abs(previous) * 100
            break
          case 'rank':
            next = own === null ? null : 1 + present.filter(number => number > own).length
            break
          case 'dense_rank':
            next = own === null ? null : 1 + ranked.indexOf(own)
            break
          case 'moving_average': {
            const size = calculation.window_size ?? 3
            const window = ordered.slice(Math.max(0, position - size + 1), position + 1)
              .map(member => raw[member]).filter((number): number is number => number !== null)
            next = own === null || !window.length ? null : window.reduce((sum, number) => sum + number, 0) / window.length
            break
          }
        }
        rows[index][target] = value(next)
      })
    }
    const field = humanize(columns[target].name)
    columns[target] = { ...columns[target], logical_type: 'float',
      name: `${CALCULATION_LABELS[calculation.kind]}${calculation.kind === 'moving_average' ? ` (${calculation.window_size})` : ''} of ${field.charAt(0).toLowerCase()}${field.slice(1)}${PERCENT_CALCULATIONS.has(calculation.kind) ? ' (%)' : ''}`,
      aggregation: null }
  }
  return { ...result, columns, rows }
}
