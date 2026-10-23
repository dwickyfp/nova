import { z } from 'zod'
import contracts from './contracts.json'

export type Scalar = string | number | boolean | null
export type FilterValue = { kind: 'unset' | 'single' | 'multi' | 'range' | 'date_range'; values: Scalar[]; minimum: Scalar; maximum: Scalar; relative: string | null }
export type Source = { id: string; view_id: string; version: number; fingerprint: string }
export type Sort = { field: string; direction: 'asc' | 'desc' }
export type Predicate = { field: string; operator: string; value: Scalar | Scalar[] }
export type Filter = { id: string; label: string; source: string; field: string; kind: 'single' | 'multi' | 'range' | 'date_range' | 'text' | 'boolean'; default: FilterValue; required: boolean; allow_null: boolean; scope: string; targets: string[]; exclude_widgets: string[]; parents: string[]; mappings: Record<string, string> }
export type Widget = {
  id: string; title: string
  kind: 'kpi' | 'bar' | 'line' | 'area' | 'table' | 'pivot' | 'stacked_bar' | 'grouped_bar' | 'combo' | 'scatter' | 'donut' | 'heatmap' | 'waterfall' | 'map' | 'funnel'
  query: { source: string; calculations?: { name: string; expression: Record<string, unknown> }[]; named_filters?: string[]; metrics: string[]; dimensions: string[]; filters: Predicate[]; time: { dimension: string; grain: string | null; range: string | null; compare: string | null } | null; order_by: Sort[]; transforms: { metric: string; kind: string }[]; limit: number | null } | null
  encoding: { x: string | null; y: string[]; series: string | null; value: string | null; latitude?: string | null; longitude?: string | null }
  format: { kind: 'number' | 'currency' | 'percent' | 'percentage_points'; precision: number; currency: string }
  placement: { col: number; row: number; width: number; height: number }
  mapping_required: boolean; legacy_artifact_id: string | null; drill_dimensions: string[]; detail_fields?: string[]; pivot_columns: string[]; conditional_formats?: { field: string; operator: '<' | '<=' | '=' | '>=' | '>'; value: string | number; tone: 'positive' | 'negative' | 'attention' }[]
}
export type ReportSpec = {
  schema_version: '1.0.0'; title: string; description: string; timezone: string; locale: string
  calendar?: { fiscal_start_month: number; week_start: number }; sources: Source[]; filters: Filter[]
  pages: { id: string; title: string; sections: { id: string; title: string; widgets: Widget[] }[] }[]
  interactions: { id: string; source_widget: string; field: string; targets: string[]; mappings: Record<string, string> }[]
}
export type Origin = { agent_id: string; thread_id: string; message_id?: string | null; content_id?: string | null }
export type ViewState = {
  schema_version: 1; session_id: string; report_id: string; draft_id: string | null; spec_revision: number; state_revision: number
  active_page_id: string; filters: Record<string, FilterValue>; cross_filters: Record<string, Scalar[]>; drill_paths: Record<string, Predicate[]>
  group_by: Record<string, string[]>; selected_widget_id: string | null; sort: Record<string, Sort[]>; page_index: Record<string, number>; page_size: number; origin: Origin | null
}
export type ReportBlock = { report_id: string; draft_id?: string | null; draft_revision?: number | null; published_revision?: number | null; title: string; fallback: string }
export type ReportContextRef = { report_id: string; view_session_id: string; state_revision: number; selected_widget_id: string | null }
export type Interaction = {
  type: 'FILTER_SET' | 'FILTER_RESET' | 'FILTER_RESET_ALL' | 'PAGE_SET' | 'WIDGET_FOCUS' | 'CROSS_FILTER_APPLY' | 'CROSS_FILTER_CLEAR' | 'DRILL_DOWN' | 'DRILL_UP' | 'DRILL_BY' | 'TABLE_SORT' | 'TABLE_PAGE'
  target?: string | null; value?: FilterValue; field?: string; values?: Scalar[]; sort?: Sort[]; page_index?: number
}
export type WidgetResult = {
  widget_id: string; status: 'success' | 'empty' | 'denied' | 'error' | 'timeout' | 'requires_mapping' | 'incompatible'
  columns: string[]; rows: Scalar[][]; totals: Record<string, Scalar>; completeness: 'complete' | 'paged' | 'truncated' | 'error'
  column_labels?: Record<string, string>; row_kinds?: ('detail' | 'subtotal')[]; has_more: boolean; not_applied: string[]; query_fingerprint: string | null; query_id: string | null; semantic_view_id: string | null; semantic_version: number | null; evaluated_at: string | null; message: string | null
  warnings?: string[]; time_context?: { current: { start: string; end: string }; baseline: { start: string; end: string } | null; timezone: string } | null
}
export type RenderResult = { report_id: string; spec_revision: number; state_revision: number; state_fingerprint: string; as_of: string; status: 'success' | 'partial_success'; widgets: WidgetResult[] }
export type Patch = { op: string; target?: string; parent?: string; value?: Record<string, unknown> }
export type PatchRequest = { operation_id: string; expected_revision: number; operations: Patch[] }
export type ReportSummary = { report_id: string; title: string; owner: string; published_revision: number; draft_id: string | null; permission: 'owner' | 'publisher' | 'editor' | 'viewer'; requires_mapping?: boolean; legacy_dashboard_id?: string; migration_status?: string; revision: number }

// Generated JSON Schema is shared with Pydantic; backend also checks semantic references.
export const reportSpecSchema = z.fromJSONSchema(contracts.ReportSpecV1 as Parameters<typeof z.fromJSONSchema>[0]) as z.ZodType<ReportSpec>
export const viewStateSchema = z.fromJSONSchema(contracts.ReportViewStateV1 as Parameters<typeof z.fromJSONSchema>[0]) as z.ZodType<ViewState>
export const reportBlockSchema = z.fromJSONSchema(contracts.ReportBlock as Parameters<typeof z.fromJSONSchema>[0]) as z.ZodType<ReportBlock>
