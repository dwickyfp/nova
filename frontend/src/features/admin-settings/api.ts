import { queryOptions } from '@tanstack/react-query'
import { api } from '@/lib/api-client'

/**
 * Client for backend/app/modules/variables/ (roadmap #8, NOVA-94 PR #102).
 *
 * Routes, verified against `backend/app/modules/variables/router.py`:
 *   GET  /variables?scope=session|global&search=&limit=&offset=
 *   POST /variables/set                  { scope, name, value?, reset }
 */

export type VariableScope = 'session' | 'global'

export type VariableItem = {
  name: string
  value: string
  /** The engine's default, when this build of StarRocks reports one. */
  default: string | null
  scope: VariableScope
}

export type VariableListResponse = {
  variables: VariableItem[]
  count: number
  total: number
  limit: number
  offset: number
  scope: VariableScope
}

export type VariableSetRequest = {
  scope: VariableScope
  name: string
  value?: string | null
  reset?: boolean
}

export type VariableSetResponse = {
  success: boolean
  name: string
  scope: VariableScope
  value: string | null
  message: string
}

export function fetchVariables(params: {
  scope: VariableScope
  search?: string
  limit?: number
  offset?: number
}) {
  const query = new URLSearchParams({
    scope: params.scope,
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
  })
  if (params.search) query.set('search', params.search)
  return api.get<VariableListResponse>(`/variables?${query.toString()}`)
}

export function setVariable(request: VariableSetRequest) {
  return api.post<VariableSetResponse>('/variables/set', request)
}

export function isVariableChanged(variable: VariableItem): boolean {
  return variable.default != null && variable.value !== variable.default
}

/** Every variable of a scope, read once: a few hundred short rows. */
const ALL_VARIABLES = 1000

export function variablesQueryOptions(scope: VariableScope) {
  return queryOptions<VariableListResponse>({
    queryKey: ['variables', scope],
    queryFn: () => fetchVariables({ scope, limit: ALL_VARIABLES, offset: 0 }),
    // Engine variables change only when someone sets one; a save refreshes.
    staleTime: 5 * 60_000,
  })
}

export const SETTINGS_TABS = ['session', 'global'] as const
export type SettingsTab = (typeof SETTINGS_TABS)[number]
