import type { ChartDefinition, DatasetField, Dimension, FilterTree } from '../contracts'
import { humanize } from './chart-query'
import type { ValueSelection } from './native-chart'

type Grain = NonNullable<Dimension['date_grain']>

/** One step into a chart: the value clicked, and what it is broken down by next. */
export type DrillStep = {
  field_id: string
  value: ValueSelection['value']
  label: string
  period?: ValueSelection['period']
  by: { field_id: string; date_grain?: Grain | null }
}

export type DrillOption = { field_id: string; label: string; date_grain?: Grain | null }

const FINER: Record<Grain, Grain | null> = { year: 'quarter', quarter: 'month', month: 'week', week: 'day', day: null }
const GRAIN_WORDS: Record<Grain, string> = { year: 'year', quarter: 'quarter', month: 'month', week: 'week', day: 'day' }
const GROUPABLE = new Set<DatasetField['logical_type']>(['string', 'boolean', 'date', 'timestamp'])

/** The single grouping a chart draws, if it has exactly one. */
export function drillDimension(chart: ChartDefinition): Dimension | null {
  const encoding = chart.encoding
  if (encoding.kind === 'cartesian' && encoding.dimensions.length === 1 && !encoding.color) return encoding.dimensions[0]
  if (encoding.kind === 'category') return encoding.category
  return null
}

/** What a clicked value can be broken down by: a finer date level, or another grouping field. */
export function drillOptions(chart: ChartDefinition, fields: DatasetField[], used: string[] = []): DrillOption[] {
  const current = drillDimension(chart)
  if (!current) return []
  const options: DrillOption[] = []
  const finer = current.date_grain ? FINER[current.date_grain] : null
  const currentField = fields.find(field => field.field_id === current.field_id)
  if (finer && currentField) {
    options.push({ field_id: current.field_id, date_grain: finer, label: `${humanize(currentField.name)} by ${GRAIN_WORDS[finer]}` })
  }
  for (const field of fields) {
    if (field.semantic_role !== 'dimension' || !GROUPABLE.has(field.logical_type)) continue
    if (field.field_id === current.field_id || used.includes(field.field_id)) continue
    const temporal = field.logical_type === 'date' || field.logical_type === 'timestamp'
    options.push({ field_id: field.field_id, label: humanize(field.name), ...(temporal ? { date_grain: 'month' as Grain } : {}) })
  }
  return options.slice(0, 12)
}

/** The chart regrouped by the last step's breakdown. */
export function drilledChart(chart: ChartDefinition, steps: DrillStep[]): ChartDefinition {
  const last = steps[steps.length - 1]
  if (!last) return chart
  const dimension: Dimension = { field_id: last.by.field_id, ...(last.by.date_grain ? { date_grain: last.by.date_grain } : {}) }
  const encoding = chart.encoding
  const next = encoding.kind === 'cartesian' ? { ...encoding, dimensions: [dimension] }
    : encoding.kind === 'category' ? { ...encoding, category: dimension }
      : encoding
  // Sorting named the old grouping; the drilled view sorts by its own default.
  return { ...chart, encoding: next, sort: (chart.sort ?? []).filter(item => item.field_id !== drillDimension(chart)?.field_id) } as ChartDefinition
}

function binding(chart: ChartDefinition, fieldId: string) {
  return { dataset_id: chart.dataset_id, dataset_revision: chart.dataset_revision, field_id: fieldId }
}

/** Every clicked value narrows the chart; added to the dashboard's own filters. */
export function drillContext(chart: ChartDefinition, steps: DrillStep[], base?: FilterTree): FilterTree | undefined {
  if (!steps.length) return base
  const parts: unknown[] = base?.expression ? [base.expression] : []
  for (const step of steps) {
    const field = binding(chart, step.field_id)
    const predicate = (op: string, values: unknown[] = []) => ({ type: 'predicate', field, op, values, source: 'global', stage: 'row' })
    if (step.period) {
      parts.push({ type: 'and', children: [
        predicate('gte', [{ type: 'date', value: step.period.from }]),
        predicate('lt', [{ type: 'date', value: step.period.to }]),
      ] })
    } else parts.push(step.value === null ? predicate('is_null') : predicate('eq', [step.value]))
  }
  return { expression: (parts.length === 1 ? parts[0] : { type: 'and', children: parts }) } as FilterTree
}
