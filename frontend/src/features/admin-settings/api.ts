import { api } from '@/lib/api-client'

/**
 * Contract for backend/app/modules/variables/ (roadmap #8, NOVA-94 backend PR 3).
 * The router is not merged as of this commit, so these calls are written to the
 * documented contract and fail with the backend's own 404 until it lands. The
 * page renders a "not available" state from that error rather than pretending
 * the surface works.
 *
 *   GET  /variables?scope=session|global&search=&limit=&offset=
 *   PUT  /variables/:name            { scope, value }
 *   GET  /variables/password-policy
 *   PUT  /variables/password-policy
 */

export type VariableScope = 'session' | 'global'

export type Variable = {
  name: string
  value: string | null
  default_value: string | null
  scope: VariableScope
  description: string | null
  /** True when the current value differs from the engine default. */
  changed?: boolean
}

export type VariablesResponse = {
  items: Variable[]
  total: number
}

export type PasswordPolicy = {
  password_lifetime: number | null
  password_history: number | null
  failed_login_attempts: number | null
  password_lock_time: number | null
  validate_password: boolean
  validate_password_length: number | null
  validate_password_mixed_case_count: number | null
  validate_password_number_count: number | null
  validate_password_special_char_count: number | null
}

export const PASSWORD_POLICY_DEFAULTS: PasswordPolicy = {
  password_lifetime: 90,
  password_history: 0,
  failed_login_attempts: 0,
  password_lock_time: 30,
  validate_password: false,
  validate_password_length: 8,
  validate_password_mixed_case_count: 1,
  validate_password_number_count: 1,
  validate_password_special_char_count: 1,
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
  return api.get<VariablesResponse>(`/variables?${query.toString()}`)
}

export function updateVariable(name: string, scope: VariableScope, value: string) {
  return api.put<Variable>(`/variables/${encodeURIComponent(name)}`, {
    scope,
    value,
  })
}

export function fetchPasswordPolicy() {
  return api.get<PasswordPolicy>('/variables/password-policy')
}

export function updatePasswordPolicy(policy: PasswordPolicy) {
  return api.put<PasswordPolicy>('/variables/password-policy', policy)
}

export function isVariableChanged(variable: Variable): boolean {
  if (typeof variable.changed === 'boolean') return variable.changed
  return variable.default_value != null && variable.value !== variable.default_value
}

export function parsePolicyNumber(value: string): number | null {
  if (value.trim() === '') return null
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0) return null
  return Math.floor(parsed)
}
