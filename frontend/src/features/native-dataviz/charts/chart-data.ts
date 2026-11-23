import { useMemo } from 'react'
import { keepPreviousData, type QueryClient, useQuery } from '@tanstack/react-query'
import { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import type { ChartDefinition, DatasetDefinition, DatasetQueryResult, FilterTree, ParameterInput, QuerySort } from '../contracts'
import { nativeDocuments } from '../documents'
import { applyCalculations } from './calculations'
import { cellNumber, chartQueryShape, ChartQueryError, combineFilters } from './chart-query'
import { capacityDelay, capacityRetry, queuedQuery } from './query-queue'

/** Refusals a Studio draft chart explains in its own words: owner, role and record checks. */
const DRAFT_REFUSALS = new Set([404, 409, 422])

export function queryError(error: unknown, source: 'dataset' | 'draft' = 'dataset'): string {
  if (error instanceof ChartQueryError) return error.message
  if (error instanceof ApiError) {
    if (source === 'draft' && DRAFT_REFUSALS.has(error.status) && error.message) return error.message
    if ([401, 403, 404].includes(error.status)) return 'This data is unavailable with your current role.'
    if (error.status === 409) return 'The data source changed since this chart was saved. Someone who can edit the dashboard can review it in the Data tab.'
    if (error.status === 413) return 'The result is too large. Reduce the row limit or add a filter.'
    if (error.status === 429) return 'Query capacity is still busy. Retry in a moment.'
    if (error.status === 503) return 'The data engine is not responding. Retry in a moment.'
    if (error.status === 504) return 'The query timed out. Add a filter or reduce the row limit.'
    if (error.status === 422) return 'This chart configuration cannot be run. Check its fields and filters.'
  }
  return 'The chart could not be loaded.'
}

type ChartRequestInput = {
  chart: ChartDefinition; draft: boolean; context?: FilterTree; parameters?: ParameterInput[]
  /** A Studio answer's unsaved chart: its rows come from the draft query, never a saved dataset. */
  draftRef?: string
}

/** One server page of a table: where it starts, how many rows, and a viewer's column sort. */
export type TablePage = { offset: number; limit: number; sort?: QuerySort | null }

/** The server's last page start; a table never asks beyond it. */
export const MAX_PAGE_OFFSET = 10_000

/** The aggregate request a chart sends, or null when its configuration cannot run. */
export function chartRequest({ chart, draft, context, parameters }: ChartRequestInput) {
  try {
    const shape = chartQueryShape(chart)
    const filters = combineFilters(chart.filters, context)
    return {
      revision: chart.dataset_revision, draft, ...shape,
      ...(filters ? { filters } : {}),
      ...(parameters?.length ? { parameters } : {}),
    }
  } catch {
    return null
  }
}

/**
 * How long a chart may show rows it already read without asking again. Ranger
 * can narrow or revoke access without changing the role, so this is short, an
 * error from the next read replaces the rows, and downloads never use it.
 */
const SHOWN_ROWS_STALE_TIME = 30_000

/** One cache entry per chart request, shared by the chart and its downloads. */
export function chartDataQuery(epoch: number, chart: ChartDefinition, request: NonNullable<ReturnType<typeof chartRequest>>,
  draftRef?: string) {
  return {
    queryKey: ['native-dataviz', 'chart-data', epoch, sourceKey(chart, draftRef), JSON.stringify(request),
      JSON.stringify(chart.table_calculations ?? [])],
    // Table calculations run on the governed result, so charts, downloads and print agree.
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      queuedQuery(() => readRows(epoch, chart, request, draftRef), signal)
        .then(result => applyCalculations(chart, result)),
    retry: capacityRetry,
    retryDelay: capacityDelay,
    staleTime: SHOWN_ROWS_STALE_TIME,
    gcTime: 5 * 60_000,
  }
}

/** Rows for a download: always read again, so a file never holds rows the current policy hides. */
export function freshChartData(client: QueryClient, epoch: number, chart: ChartDefinition,
  request: NonNullable<ReturnType<typeof chartRequest>>, draftRef?: string) {
  return client.fetchQuery({ ...chartDataQuery(epoch, chart, request, draftRef), staleTime: 0 })
}

function sourceKey(chart: ChartDefinition, draftRef?: string) {
  return draftRef ? `draft:${draftRef}` : chart.dataset_id
}

function readRows(epoch: number, chart: ChartDefinition, request: NonNullable<ReturnType<typeof chartRequest>>,
  draftRef?: string) {
  const documents = nativeDocuments(epoch)
  return draftRef
    ? documents.queryDraftChart(draftRef, chart.dataset_id, request)
    : documents.queryDataset(chart.dataset_id, request)
}

/** The same governed request narrowed to one page; a viewer sort replaces the chart's. */
export function pagedRequest(request: NonNullable<ReturnType<typeof chartRequest>>, page: TablePage) {
  return {
    ...request,
    ...(page.sort ? { sort: [page.sort] } : {}),
    limit: page.limit,
    ...(page.offset ? { offset: page.offset } : {}),
  }
}

/** The measure totals over every matching row, for a table read one page at a time. */
export function totalsRequest(request: NonNullable<ReturnType<typeof chartRequest>>) {
  if (!request.measures?.length) return null
  const { dimensions: _dimensions, sort: _sort, ...rest } = request
  return { ...rest, limit: 1 }
}

export function useChartData({ chart, draft, context, parameters, page, draftRef }: ChartRequestInput & { page?: TablePage | null }) {
  const epoch = useAuthStore(state => state.securityEpoch)
  const request = useMemo(() => {
    const full = chartRequest({ chart, draft, context, parameters })
    return full && page ? pagedRequest(full, page) : full
  }, [chart, draft, context, parameters, page])
  const options = request ? chartDataQuery(epoch, chart, request, draftRef) : null
  return useQuery({
    queryKey: options?.queryKey ?? ['native-dataviz', 'chart-data', epoch, sourceKey(chart, draftRef), 'null'],
    queryFn: options?.queryFn ?? (() => Promise.reject(new Error('unreachable'))),
    enabled: request !== null,
    placeholderData: keepPreviousData,
    retry: capacityRetry,
    retryDelay: capacityDelay,
    staleTime: SHOWN_ROWS_STALE_TIME,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })
}

/** Grand totals for a paged table: one measures-only query, shared by every page. */
/** Aggregations whose total is the sum of its parts, so the rest is total minus the shown parts. */
const ADDITIVE = new Set(['sum', 'count'])

/** A capped pie set to combine the rest into Other, with a measure that allows it. */
export function combinesOther(chart: ChartDefinition): boolean {
  const encoding = chart.encoding
  return encoding.kind === 'category' && encoding.overflow === 'other' && ADDITIVE.has(encoding.measure.aggregation ?? '')
}

/** The exact value of Other: the governed total less the slices shown. */
export function remainderOf(result: DatasetQueryResult, totals: DatasetQueryResult | undefined): number | null {
  const total = totals ? cellNumber(totals.rows[0]?.[0] ?? null) : null
  if (total === null) return null
  const shown = result.rows.reduce((sum, row) => sum + (cellNumber(row[1]) ?? 0), 0)
  const rest = total - shown
  return rest > 0 ? rest : null
}

export function useTableTotals({ chart, draft, context, parameters, enabled, draftRef }: ChartRequestInput & { enabled: boolean }) {
  const epoch = useAuthStore(state => state.securityEpoch)
  const request = useMemo(() => {
    const full = chartRequest({ chart, draft, context, parameters })
    return full ? totalsRequest(full) : null
  }, [chart, draft, context, parameters])
  return useQuery({
    queryKey: ['native-dataviz', 'chart-totals', epoch, sourceKey(chart, draftRef), JSON.stringify(request)],
    queryFn: ({ signal }) => queuedQuery(() => readRows(epoch, chart, request!, draftRef), signal),
    enabled: enabled && request !== null,
    retry: capacityRetry,
    retryDelay: capacityDelay,
    staleTime: SHOWN_ROWS_STALE_TIME,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })
}

export function useDatasetDefinition(datasetId: string | undefined, revision: number | undefined) {
  const epoch = useAuthStore(state => state.securityEpoch)
  return useQuery({
    queryKey: ['native-dataviz', 'dataset-definition', epoch, datasetId, revision],
    queryFn: async ({ signal }) => {
      const snapshot = await nativeDocuments(epoch).read('datasets', datasetId!, { draft: true, revision, signal })
      return snapshot.definition as DatasetDefinition
    },
    enabled: Boolean(datasetId && revision),
    staleTime: Infinity, retry: false,
  })
}
