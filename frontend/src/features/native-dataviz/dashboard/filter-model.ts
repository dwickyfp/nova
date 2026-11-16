import type { ChartDefinition, DashboardDefinition, FilterTree } from '../contracts'
import type { Cell } from '../charts/chart-query'

export type FilterDefinition = NonNullable<DashboardDefinition['filters']>[number]
type Scalar = NonNullable<Cell>
type Expression = FilterTree['expression']
type Binding = { dataset_id: string; dataset_revision: number; field_id: string }

export type FilterValue =
  | { kind: 'members'; values: (Scalar | null)[] }
  | { kind: 'range'; from: Scalar | null; to: Scalar | null }
  | { kind: 'text'; value: string }
  | { kind: 'relative'; unit: 'day' | 'week' | 'month' | 'quarter' | 'year'; count: number; mode: 'previous' | 'rolling' | 'to_date' }
  | { kind: 'boolean'; value: boolean }

export type CrossFilter = { source_widget_id: string; field: Binding; value: Scalar | null; label: string; period?: { from: string; to: string } }

export function nextDay(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day + 1)).toISOString().slice(0, 10)
}

export function predicate(field: Binding, op: string, values: Scalar[] = [], extra: Record<string, unknown> = {}): Expression {
  return { type: 'predicate', field, op, values, source: 'global', stage: 'row', ...extra } as Expression
}

function expressionFor(field: Binding, value: FilterValue): Expression | null {
  switch (value.kind) {
    case 'members': {
      if (!value.values.length) return null
      const present = value.values.filter((item): item is Scalar => item !== null)
      const parts: Expression[] = []
      if (present.length === 1) parts.push(predicate(field, 'eq', present))
      else if (present.length) parts.push(predicate(field, 'in', present))
      if (value.values.includes(null)) parts.push(predicate(field, 'is_null'))
      return parts.length === 1 ? parts[0] : { type: 'or', children: parts }
    }
    case 'range': {
      // A date range includes its whole end day: [from, to + 1 day). On a
      // timestamp column, `<= to` would drop everything after its midnight.
      const upper = value.to && value.to.type === 'date'
        ? predicate(field, 'lt', [{ type: 'date', value: nextDay(value.to.value) }])
        : value.to ? predicate(field, 'lte', [value.to]) : null
      const lower = value.from ? predicate(field, 'gte', [value.from]) : null
      if (lower && upper) return { type: 'and', children: [lower, upper] }
      return lower ?? upper
    }
    case 'text':
      return value.value.trim() ? predicate(field, 'like_literal', [{ type: 'string', value: value.value.trim() }]) : null
    case 'relative':
      return predicate(field, 'relative_date', [], { relative: {
        unit: value.unit, count: value.count, mode: value.mode,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', week_start: 'iso', fiscal_start_month: 1,
      } })
    case 'boolean':
      return predicate(field, 'eq', [{ type: 'boolean', value: value.value }])
  }
}

function inScope(filter: FilterDefinition, widgetId: string, pageId: string): boolean {
  const scope = filter.scope
  if (scope.kind === 'global') return true
  if (scope.kind === 'page') return scope.page_id === pageId
  return (scope.widget_ids ?? []).includes(widgetId)
}

function sameDataset(binding: Binding, chart: Pick<ChartDefinition, 'dataset_id' | 'dataset_revision'>) {
  return binding.dataset_id === chart.dataset_id && binding.dataset_revision === chart.dataset_revision
}

/** The field a filter targets in one chart's dataset: its own binding or an explicit mapping. */
export function targetBinding(filter: Pick<FilterDefinition, 'field' | 'mappings'>, chart: Pick<ChartDefinition, 'dataset_id' | 'dataset_revision'>): Binding | null {
  if (filter.field && sameDataset(filter.field, chart)) return filter.field
  const mapping = (filter.mappings ?? []).find(item => sameDataset(item.target, chart))
  return mapping?.target ?? null
}

/** Typed predicates from viewer state for one chart widget. Filters never apply to unrelated datasets. */
export function chartContext(input: {
  definition: DashboardDefinition; chart: ChartDefinition; widgetId: string; pageId: string
  values: Record<string, FilterValue>; crossFilters: CrossFilter[]
}): FilterTree | undefined {
  const parts: Expression[] = []
  for (const filter of input.definition.filters ?? []) {
    const value = input.values[filter.filter_id]
    if (!value || !inScope(filter, input.widgetId, input.pageId)) continue
    const binding = targetBinding(filter, input.chart)
    const expression = binding && expressionFor(binding, value)
    if (expression) parts.push(expression)
  }
  // Values selected together in one chart are alternatives (OR); different
  // charts and fields narrow each other (AND).
  const groups = new Map<string, Expression[]>()
  for (const cross of input.crossFilters) {
    if (cross.source_widget_id === input.widgetId || !sameDataset(cross.field, input.chart)) continue
    const expression: Expression = cross.period
      ? { type: 'and', children: [
        predicate(cross.field, 'gte', [{ type: 'date', value: cross.period.from }]),
        predicate(cross.field, 'lt', [{ type: 'date', value: cross.period.to }]),
      ] }
      : cross.value === null ? predicate(cross.field, 'is_null') : predicate(cross.field, 'eq', [cross.value])
    const key = `${cross.source_widget_id}|${cross.field.field_id}`
    groups.set(key, [...(groups.get(key) ?? []), expression])
  }
  for (const alternatives of groups.values()) {
    parts.push(alternatives.length === 1 ? alternatives[0] : { type: 'or', children: alternatives })
  }
  if (!parts.length) return undefined
  return { expression: parts.length === 1 ? parts[0] : { type: 'and', children: parts } }
}

export function defaultValue(filter: FilterDefinition): FilterValue | undefined {
  const defaults = (filter.defaults ?? []) as Scalar[]
  if (filter.kind === 'relative_date' && filter.relative) {
    return { kind: 'relative', unit: filter.relative.unit, count: filter.relative.count ?? 1, mode: filter.relative.mode ?? 'rolling' }
  }
  if (!defaults.length) return undefined
  if (filter.kind === 'date_range' || filter.kind === 'number_range_slider') return { kind: 'range', from: defaults[0], to: defaults[1] ?? null }
  if (filter.kind === 'text_entry' && defaults[0].type === 'string') return { kind: 'text', value: defaults[0].value }
  if (filter.kind === 'boolean_switch' && defaults[0].type === 'boolean') return { kind: 'boolean', value: defaults[0].value }
  return { kind: 'members', values: defaults }
}

