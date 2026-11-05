import { useQuery } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth-store'
import { useBiSession } from './use-bi-session'

type DashboardMetadata = { result?: { json_metadata?: string | null } }

/** Superset's native filters on a dashboard, read from its JSON metadata. */
export function countNativeFilters(metadata: DashboardMetadata): number {
  try {
    const parsed = JSON.parse(metadata.result?.json_metadata || '{}') as {
      native_filter_configuration?: unknown[]
    }
    return Array.isArray(parsed.native_filter_configuration)
      ? parsed.native_filter_configuration.length
      : 0
  } catch {
    return 0
  }
}

/** The dashboard view without Superset's filter bar. */
export function withoutFilterBar(path: string): string {
  const url = new URL(path, window.location.origin)
  url.searchParams.set('show_filters', '0')
  return `${url.pathname}${url.search}${url.hash}`
}

/**
 * Whether a BI dashboard has native filters: true or false once known, null
 * while Superset is asked. A view of a dashboard without filters hides
 * Superset's empty filter bar; when the answer is unavailable the bar stays,
 * as Superset shows it by default. Superset's session cookie signs the request
 * in, so the answer is the viewer's own.
 */
export function useDashboardHasFilters(dashboardId: number | null, enabled = true): boolean | null {
  const session = useBiSession()
  const securityEpoch = useAuthStore((state) => state.securityEpoch)
  const active = enabled && dashboardId !== null
  const query = useQuery({
    queryKey: ['bi', 'dashboard-filters', dashboardId, securityEpoch],
    enabled: active && session.status === 'ready',
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 0,
    queryFn: async ({ signal }) => {
      const response = await fetch(`/bi/api/v1/dashboard/${dashboardId}`, { signal })
      if (!response.ok) throw new Error(`Dashboard unavailable (${response.status})`)
      return countNativeFilters((await response.json()) as DashboardMetadata) > 0
    },
  })
  if (!active || session.status === 'error' || query.isError) return true
  return query.data ?? null
}
