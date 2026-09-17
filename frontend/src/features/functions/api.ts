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

export const createUDF = async (payload: CreateUDFPayload) =>
  api.post('/functions/udf', payload)

export const deleteUDF = async (fn: Pick<UserDefinedFunction, 'database' | 'name'>) =>
  api.delete(`/functions/udf/${encodeURIComponent(fn.database)}/${encodeURIComponent(fn.name)}`)
