import type { NativeViewFilter } from '@/features/agents/api'
import type { DashboardDefinition } from '../contracts'
import { describeFilterValue } from './filter-describe'
import type { CrossFilter, FilterValue } from './filter-model'

type Raw = string | number | boolean | null

function raw(cell: { value?: unknown } | null | undefined): Raw {
  const value = cell?.value
  if (value === undefined || value === null) return null
  if (typeof value === 'number' || typeof value === 'boolean') return value
  return String(value).slice(0, 256)
}

function values(value: FilterValue): Raw[] {
  switch (value.kind) {
    case 'members': return value.values.slice(0, 20).map(raw)
    case 'range': return [raw(value.from), raw(value.to)]
    case 'text': return [value.value.slice(0, 256)]
    case 'boolean': return [value.value]
    default: return []
  }
}

/** What is filtered or selected on screen, bounded, for Neva's next answer. */
export function viewFilters(definition: DashboardDefinition, applied: Record<string, FilterValue>, crossFilters: CrossFilter[]): NativeViewFilter[] {
  const items: NativeViewFilter[] = []
  for (const filter of definition.filters ?? []) {
    const value = applied[filter.filter_id]
    const text = describeFilterValue(value)
    if (!value || text === 'All') continue
    items.push({ source: 'filter', label: filter.title.slice(0, 128) || 'Filter', text: text.slice(0, 256),
      dataset_id: filter.field?.dataset_id ?? null, field_id: filter.field?.field_id ?? null, values: values(value) })
  }
  for (const item of crossFilters) {
    items.push({ source: 'selection', label: item.label.slice(0, 128) || 'Selection', text: `Selected ${item.label}`.slice(0, 256),
      dataset_id: item.field.dataset_id, field_id: item.field.field_id,
      values: item.period ? [item.period.from, item.period.to] : [raw(item.value)] })
  }
  return items.slice(0, 32)
}
