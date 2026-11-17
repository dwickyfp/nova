import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'

export type DashboardComment = {
  comment_id: string
  page_id: string | null
  widget_id: string | null
  author: string
  body: string
  created_at: string
  resolved_at: string | null
  resolved_by: string | null
  mine: boolean
}

export const MAX_COMMENT = 2000

export function commentsKey(epoch: number, dashboardId: string) {
  return ['native-dataviz', 'comments', epoch, dashboardId]
}

/** How many open comments a dashboard has, for the header button. */
export function useComments(dashboardId: string, enabled = true) {
  const epoch = useAuthStore(state => state.securityEpoch)
  return useQuery({
    queryKey: commentsKey(epoch, dashboardId), enabled, retry: false, refetchInterval: 30_000,
    queryFn: ({ signal }) => api.request<DashboardComment[]>(
      `/viz/dashboards/${encodeURIComponent(dashboardId)}/comments`, { signal, securityEpoch: epoch }),
  })
}
