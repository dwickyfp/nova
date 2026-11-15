import { useMemo } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { workspaceApi, type WorkspaceSpace } from '@/features/workspaces/explorer/api'
import { useAuthStore } from '@/stores/auth-store'
import type { ChartDefinition, DocumentSummary } from '../contracts'
import { nativeDocuments } from '../documents'

export function useChartDocument(chartId: string, revision: number, draft: boolean) {
  const epoch = useAuthStore(state => state.securityEpoch)
  return useQuery({
    queryKey: ['native-dataviz', 'chart', epoch, chartId, revision, draft],
    // Pinned revisions are immutable; draft scope requires edit access, otherwise
    // the publication that pins this revision must be visible to the viewer.
    queryFn: async ({ signal }) => (await nativeDocuments(epoch).read('charts', chartId, { draft, revision, signal })).definition as ChartDefinition,
    staleTime: Infinity, retry: false,
  })
}

export type DashboardEntry = DocumentSummary & { editable: boolean; space: WorkspaceSpace }

const PERSONAL_SPACE: WorkspaceSpace = { id: 'personal', name: 'Personal', kind: 'personal', owner: '', permission: 'owner' }

export function useDashboards() {
  const epoch = useAuthStore(state => state.securityEpoch)
  const spaces = useQuery({
    queryKey: ['native-dataviz', 'spaces', epoch],
    queryFn: async () => {
      const result = await workspaceApi.spaces()
      const list = result.spaces ?? []
      return list.some(space => space.id === 'personal') ? list : [PERSONAL_SPACE, ...list]
    },
    staleTime: 60_000, retry: false,
  })
  const lists = useQueries({
    queries: (spaces.data ?? [PERSONAL_SPACE]).flatMap(space => [true, false].map(draft => ({
      queryKey: ['native-dataviz', 'dashboards', epoch, space.id, draft],
      queryFn: ({ signal }: { signal: AbortSignal }) => nativeDocuments(epoch).list('dashboards', space.id, { draft, limit: 100, signal }),
      enabled: spaces.isFetched, retry: false,
    }))),
  })
  const entries = useMemo(() => {
    const seen = new Map<string, DashboardEntry>()
    const all = spaces.data ?? [PERSONAL_SPACE]
    all.forEach((space, index) => {
      const drafts = lists[index * 2]?.data ?? []
      const publications = lists[index * 2 + 1]?.data ?? []
      for (const item of drafts) seen.set(item.resource_id, { ...item, editable: true, space })
      for (const item of publications) if (!seen.has(item.resource_id)) seen.set(item.resource_id, { ...item, editable: false, space })
    })
    return [...seen.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  }, [lists, spaces.data])
  return {
    entries, spaces: spaces.data ?? [PERSONAL_SPACE],
    pending: spaces.isPending || lists.some(item => item.isPending),
    error: spaces.isError && lists.every(item => item.isError),
    refetch: () => { void spaces.refetch(); lists.forEach(item => void item.refetch()) },
  }
}

