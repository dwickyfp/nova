import type { DatasetField } from '../contracts'
import { cellLabel, type Cell } from '../charts/chart-query'
import type { FilterValue } from './filter-model'

type Scalar = NonNullable<Cell>

/** A filter operand as short text: dates as "16 Nov 2026". */
export function shortValue(cell: Scalar | null): string {
  if (cell && (cell.type === 'date' || cell.type === 'timestamp')) {
    const [year, month, day] = cell.value.slice(0, 10).split('-').map(Number)
    return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
      .format(new Date(Date.UTC(year, month - 1, day)))
  }
  return cellLabel(cell)
}

/** A filter value as one short phrase, for chips, labels and printed reports. */
export function describeFilterValue(value: FilterValue | undefined, field?: DatasetField): string {
  if (!value) return 'All'
  switch (value.kind) {
    case 'members': return value.values.length ? value.values.length === 1 ? cellLabel(value.values[0]) : `${value.values.length} selected` : 'All'
    case 'range': return value.from || value.to ? value.from && value.to ? `${shortValue(value.from)} – ${shortValue(value.to)}`
      : value.from ? `from ${shortValue(value.from)}` : `until ${shortValue(value.to)}` : 'All'
    case 'text': return value.value ? `Contains “${value.value}”` : 'All'
    case 'relative': return `${value.mode === 'previous' ? 'Previous' : value.mode === 'to_date' ? 'This' : 'Last'} ${value.mode === 'to_date' ? value.unit : `${value.count} ${value.unit}${value.count > 1 ? 's' : ''}`}${value.mode === 'to_date' ? ' to date' : ''}`
    case 'boolean': return value.value === null ? 'No value' : value.value ? 'True' : 'False'
  }
  return field ? field.name : 'All'
}
