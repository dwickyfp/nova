import type { ChartDefinition, ConditionalFormat, DatasetQueryResult } from '../contracts'
import { cellLabel, cellNumber, columnLabel } from './chart-query'

type Cell = DatasetQueryResult['rows'][number][number]

export const MAX_PIVOT_COLUMNS = 60

export type PivotModel = {
  rowHeaders: string[]
  columnKeys: string[][]
  measures: { label: string; field_id: string | null; aggregation: string | null }[]
  rows: { key: string[]; values: (number | null)[][]; subtotal?: boolean }[]
  /** Per column key and measure, when every measure adds up. */
  columnTotals: (number | null)[][] | null
  /** Per row and measure, across every column key. */
  rowTotals: (number | null)[][] | null
  truncatedColumns: boolean
}

const ADDITIVE = new Set(['sum', 'count'])

function sortKey(cell: Cell): string | number {
  if (cell === null) return '￿'
  const number = cellNumber(cell)
  return number !== null ? number : String(cell.value)
}

function compare(a: (string | number)[], b: (string | number)[]): number {
  for (let i = 0; i < a.length; i++) {
    if (a[i] === b[i]) continue
    if (typeof a[i] === 'number' && typeof b[i] === 'number') return (a[i] as number) - (b[i] as number)
    return String(a[i]).localeCompare(String(b[i]))
  }
  return 0
}

function add(left: number | null, right: number | null): number | null {
  if (right === null) return left
  return (left ?? 0) + right
}

/** A cross-tab: row groups down, column groups across, one cell per measure. */
export function pivotModel(chart: ChartDefinition, result: DatasetQueryResult): PivotModel {
  const encoding = chart.encoding.kind === 'table' ? chart.encoding : null
  const rowCount = encoding?.dimensions?.length ?? 0
  const columnCount = encoding?.pivot_columns?.length ?? 0
  const measureColumns = result.columns.slice(rowCount + columnCount)
  const measures = measureColumns.map(column => ({
    label: columnLabel(column), field_id: column.field_id ?? null, aggregation: column.aggregation ?? null,
  }))
  const columns = new Map<string, { labels: string[]; sort: (string | number)[] }>()
  const rows = new Map<string, { labels: string[]; sort: (string | number)[]; cells: Map<string, (number | null)[]> }>()
  for (const row of result.rows) {
    const rowCells = row.slice(0, rowCount)
    const columnCells = row.slice(rowCount, rowCount + columnCount)
    const rowLabels = rowCells.map((cell, i) => cellLabel(cell, result.columns[i]))
    const columnLabels = columnCells.map((cell, i) => cellLabel(cell, result.columns[rowCount + i]))
    const rowId = JSON.stringify(rowLabels)
    const columnId = JSON.stringify(columnLabels)
    if (!columns.has(columnId)) columns.set(columnId, { labels: columnLabels, sort: columnCells.map(sortKey) })
    if (!rows.has(rowId)) rows.set(rowId, { labels: rowLabels, sort: rowCells.map(sortKey), cells: new Map() })
    rows.get(rowId)!.cells.set(columnId, row.slice(rowCount + columnCount).map(cellNumber))
  }
  const orderedColumns = [...columns.entries()].sort((a, b) => compare(a[1].sort, b[1].sort))
  const truncatedColumns = orderedColumns.length > MAX_PIVOT_COLUMNS
  const keptColumns = orderedColumns.slice(0, MAX_PIVOT_COLUMNS)
  const additive = measures.map(measure => ADDITIVE.has(measure.aggregation ?? ''))
  const body: PivotModel['rows'] = []
  const orderedRows = [...rows.values()].sort((a, b) => compare(a.sort, b.sort))
  const valuesOf = (cells: Map<string, (number | null)[]>) =>
    keptColumns.map(([id]) => cells.get(id) ?? measures.map(() => null))
  let group: string | null = null
  let groupTotals: (number | null)[][] | null = null
  const flushGroup = () => {
    if (encoding?.subtotals && rowCount > 1 && group !== null && groupTotals) {
      body.push({ key: [group, ...Array(rowCount - 1).fill('')], values: groupTotals, subtotal: true })
    }
  }
  for (const row of orderedRows) {
    const values = valuesOf(row.cells)
    if (encoding?.subtotals && rowCount > 1 && row.labels[0] !== group) {
      flushGroup()
      group = row.labels[0]
      groupTotals = keptColumns.map(() => measures.map(() => null))
    }
    if (groupTotals) {
      values.forEach((cells, c) => cells.forEach((value, m) => {
        if (additive[m]) groupTotals![c][m] = add(groupTotals![c][m], value)
      }))
    }
    body.push({ key: row.labels, values })
  }
  flushGroup()
  const totals = Boolean(encoding?.grand_total)
  const plain = body.filter(row => !row.subtotal)
  const columnTotals = totals ? keptColumns.map((_, c) => measures.map((_, m) => additive[m]
    ? plain.reduce<number | null>((sum, row) => add(sum, row.values[c][m]), null) : null)) : null
  const rowTotals = totals ? body.map(row => measures.map((_, m) => additive[m]
    ? row.values.reduce<number | null>((sum, cells) => add(sum, cells[m]), null) : null)) : null
  return {
    rowHeaders: result.columns.slice(0, rowCount).map(column => columnLabel(column)),
    columnKeys: keptColumns.map(([, column]) => column.labels),
    measures, rows: body, columnTotals, rowTotals, truncatedColumns,
  }
}

const TONES: Record<ConditionalFormat['tone'] & string, string> = {
  accent: 'var(--chart-4)', positive: 'var(--success)', negative: 'var(--destructive)', warning: 'var(--warning)',
}

function matches(format: ConditionalFormat, value: number): boolean {
  const target = Number(format.value?.value)
  if (!Number.isFinite(target)) return false
  switch (format.operator) {
    case 'gt': return value > target
    case 'gte': return value >= target
    case 'lt': return value < target
    case 'lte': return value <= target
    case 'eq': return value === target
    default: return false
  }
}

/** Value range per measure field, for colour scales. */
export function ranges(values: { field_id: string | null; value: number | null }[]): Map<string, { min: number; max: number }> {
  const out = new Map<string, { min: number; max: number }>()
  for (const { field_id, value } of values) {
    if (field_id === null || value === null) continue
    const current = out.get(field_id)
    out.set(field_id, current ? { min: Math.min(current.min, value), max: Math.max(current.max, value) } : { min: value, max: value })
  }
  return out
}

/** The last rule for this field that the value passes; it decides the cell's highlight. */
export function matchedRule(formats: readonly ConditionalFormat[] | undefined, fieldId: string | null,
  value: number | null): ConditionalFormat | undefined {
  if (!formats?.length || fieldId === null || value === null) return undefined
  return [...formats].reverse().find(format => format.field_id === fieldId && format.kind === 'rule' && matches(format, value))
}

const RULE_WORDS: Record<NonNullable<ConditionalFormat['operator']>, string> = {
  gt: 'above', gte: 'at least', lt: 'below', lte: 'at most', eq: 'equal to',
}

/** What a highlight means in words, for people who cannot tell it by colour. */
export function ruleMeaning(rule: ConditionalFormat, format: (value: number) => string): string {
  const target = Number(rule.value?.value)
  const words = rule.operator ? RULE_WORDS[rule.operator] : null
  return words && Number.isFinite(target) ? `Highlighted: ${words} ${format(target)}` : 'Highlighted'
}

/** The cell's shading from the chart's formats: a matching rule wins over a scale. */
export function cellStyle(formats: readonly ConditionalFormat[] | undefined, fieldId: string | null, value: number | null,
  range?: { min: number; max: number }): { backgroundColor?: string; fontWeight?: number } | undefined {
  if (!formats?.length || fieldId === null || value === null) return undefined
  const own = formats.filter(format => format.field_id === fieldId)
  const rule = matchedRule(own, fieldId, value)
  // Weight and the cell's marker carry the meaning without colour.
  if (rule) return { backgroundColor: `color-mix(in oklab, ${TONES[rule.tone ?? 'accent']} 22%, transparent)`, fontWeight: 600 }
  const scale = own.find(format => format.kind === 'scale')
  if (!scale || !range) return undefined
  const share = range.max > range.min ? (value - range.min) / (range.max - range.min) : 1
  return { backgroundColor: `color-mix(in oklab, ${TONES[scale.tone ?? 'accent']} ${Math.round(6 + share * 34)}%, transparent)` }
}
