import { api } from '@/lib/api-client'

/**
 * Client for backend/app/modules/variables/ (roadmap #8, NOVA-94 PR #102).
 *
 * Routes, verified against `backend/app/modules/variables/router.py`:
 *   GET  /variables?scope=session|global&search=&limit=&offset=
 *   POST /variables/set                  { scope, name, value?, reset }
 *   GET  /variables/password-policy      -> { policy: { <name>: <string> } }
 *   POST /variables/password-policy      { <only the changed fields> }
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

/** Only the fields the caller supplies are written; every field is optional. */
export type PasswordPolicyPatch = {
  password_lifetime?: number | null
  password_history?: number | null
  failed_login_attempts?: number | null
  password_lock_time?: number | null
  validate_password?: boolean | null
  validate_password_length?: number | null
  validate_password_mixed_case_count?: number | null
  validate_password_number_count?: number | null
  validate_password_special_char_count?: number | null
}

/** The resolved policy: the nine documented names, values as strings. */
export type PasswordPolicy = Record<keyof PasswordPolicyPatch, string>

export const PASSWORD_POLICY_VARIABLES: Array<keyof PasswordPolicyPatch> = [
  'password_lifetime',
  'password_history',
  'failed_login_attempts',
  'password_lock_time',
  'validate_password',
  'validate_password_length',
  'validate_password_mixed_case_count',
  'validate_password_number_count',
  'validate_password_special_char_count',
]

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

export function fetchPasswordPolicy() {
  return api
    .get<{ policy: Record<string, string> }>('/variables/password-policy')
    .then((response) => parsePasswordPolicy(response.policy))
}

export function updatePasswordPolicy(patch: PasswordPolicyPatch) {
  return api.post<{ success: boolean; updated: number }>(
    '/variables/password-policy',
    patch
  )
}

/**
 * The GET returns the nine documented names with string values (`""` when the
 * engine did not report one). Normalise to the full shape so the form always
 * renders every field.
 */
export function parsePasswordPolicy(
  policy: Record<string, string> | null | undefined
): PasswordPolicy {
  const resolved = {} as PasswordPolicy
  for (const name of PASSWORD_POLICY_VARIABLES) {
    resolved[name] = policy?.[name] ?? ''
  }
  return resolved
}

export function isVariableChanged(variable: VariableItem): boolean {
  return variable.default != null && variable.value !== variable.default
}

export function parsePolicyNumber(value: string): number | null {
  if (value.trim() === '') return null
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0) return null
  return Math.floor(parsed)
}

export function parsePolicyBoolean(value: string): boolean {
  const normalized = value.trim().toLowerCase()
  return normalized === 'true' || normalized === 'on' || normalized === '1'
}
