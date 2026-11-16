import { useMemo } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import type { ChartDefinition, DatasetDefinition, FilterTree, ParameterInput } from '../contracts'
import { nativeDocuments } from '../documents'
import { applyCalculations } from './calculations'
import { chartQueryShape, ChartQueryError, combineFilters } from './chart-query'
import { capacityDelay, capacityRetry, queuedQuery } from './query-queue'

export function queryError(error: unknown): string {
  if (error instanceof ChartQueryError) return error.message
  if (error instanceof ApiError) {
    if ([401, 403, 404].includes(error.status)) return 'This data is unavailable with your current role.'
    if (error.status === 409) return 'The data source changed. Open the Data tab to review it.'
    if (error.status === 413) return 'The result is too large. Reduce the row limit or add a filter.'
    if (error.status === 429) return 'Query capacity is still busy. Retry in a moment.'
    if (error.status === 504) return 'The query timed out. Add a filter or reduce the row limit.'
    if (error.status === 422) return 'This chart configuration cannot be run. Check its fields and filters.'
  }
  return 'The chart could not be loaded.'
}

type ChartRequestInput = {
  chart: ChartDefinition; draft: boolean; context?: FilterTree; parameters?: ParameterInput[]
}

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

/** One cache entry per chart request, shared by the chart and its downloads. */
export function chartDataQuery(epoch: number, chart: ChartDefinition, request: NonNullable<ReturnType<typeof chartRequest>>) {
  return {
    queryKey: ['native-dataviz', 'chart-data', epoch, chart.dataset_id, JSON.stringify(request),
      JSON.stringify(chart.table_calculations ?? [])],
    // Table calculations run on the governed result, so charts, downloads and print agree.
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      queuedQuery(() => nativeDocuments(epoch).queryDataset(chart.dataset_id, request, { signal }), signal)
        .then(result => applyCalculations(chart, result)),
    retry: capacityRetry,
    retryDelay: capacityDelay,
    staleTime: 30_000,
    gcTime: 5 * 60_000,
  }
}

export function useChartData({ chart, draft, context, parameters }: ChartRequestInput) {
  const epoch = useAuthStore(state => state.securityEpoch)
  const request = useMemo(() => chartRequest({ chart, draft, context, parameters }),
    [chart, draft, context, parameters])
  const options = request ? chartDataQuery(epoch, chart, request) : null
  return useQuery({
    queryKey: options?.queryKey ?? ['native-dataviz', 'chart-data', epoch, chart.dataset_id, 'null'],
    queryFn: options?.queryFn ?? (() => Promise.reject(new Error('unreachable'))),
    enabled: request !== null,
    placeholderData: keepPreviousData,
    retry: capacityRetry,
    retryDelay: capacityDelay,
    staleTime: 30_000,
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
