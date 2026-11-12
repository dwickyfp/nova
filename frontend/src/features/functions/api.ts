import { api } from '@/lib/api-client'

export interface BuiltinFunction {
  name: string
  category: string
  signature: string
  return_type: string
  description: string
}

export interface FunctionCategory {
  name: string
  count: number
}

export interface BuiltinFunctionsResponse {
  functions: BuiltinFunction[]
  categories: FunctionCategory[]
  count: number
}

export interface UserDefinedFunction {
  name: string
  database: string
  function_type: string
  scope: string
  args: string
  return_type: string
  body: string | null
}

export interface UDFListResponse {
  functions: UserDefinedFunction[]
  databases: string[]
  count: number
}

export interface CreateUDFPayload {
  name: string
  database: string
  function_type: string
  args: string
  return_type: string
  body: string
}

// Paths are relative to the api-client base (/api/v1), which owns the prefix.
// The router serves built-ins at the module root; there is no /functions/builtin.
export const fetchBuiltinFunctions = async (): Promise<BuiltinFunctionsResponse> =>
  api.get<BuiltinFunctionsResponse>('/functions')

export const fetchUDFs = async (): Promise<UDFListResponse> => {
  const res = await api.get<UDFListResponse>('/functions/udf')
  return { ...res, databases: res.databases ?? [] }
}

/** Databases the caller's active role can use, for choosing where a new UDF goes. */
export const fetchCreatableDatabases = async (): Promise<string[]> => {
  const res = await api.get<{ databases?: string[] }>('/query/context')
  return res.databases ?? []
}

export interface UDFArgument {
  name: string
  type: string
}

/**
 * Split the dialog's "x INTEGER, y DECIMAL(10,2)" text into named, typed
 * arguments. Commas inside a type's parentheses do not separate arguments.
 */
export function parseUDFArgs(text: string): UDFArgument[] {
  const parts: string[] = []
  let depth = 0
  let current = ''
  for (const char of text) {
    if (char === '(') depth += 1
    if (char === ')') depth = Math.max(0, depth - 1)
    if (char === ',' && depth === 0) {
      parts.push(current)
      current = ''
    } else {
      current += char
    }
  }
  parts.push(current)
  return parts
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const match = /^(\S+)\s+(.+)$/.exec(part)
      if (!match) throw new Error(`Give each argument a name and a type, like "x INTEGER" (got "${part}")`)
      return { name: match[1], type: match[2].trim() }
    })
}

export const createUDF = async (payload: CreateUDFPayload) =>
  api.post('/functions/udf', { ...payload, args: parseUDFArgs(payload.args) })

export const deleteUDF = async (fn: Pick<UserDefinedFunction, 'database' | 'name'>) =>
  api.delete(`/functions/udf/${encodeURIComponent(fn.database)}/${encodeURIComponent(fn.name)}`)
