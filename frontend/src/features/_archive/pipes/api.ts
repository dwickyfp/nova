import { api } from '@/lib/api-client'

export type PipeState = 'RUNNING' | 'SUSPENDED' | 'ERROR'
export type FileState = 'LOADED' | 'LOADING' | 'ERROR'

export interface Pipe {
  name: string
  state: PipeState
  database: string
  sql: string
  auto_ingest: boolean
  poll_interval: number
  batch_size: string
  batch_files: number
}

export interface PipeFile {
  file_name: string
  state: FileState
  file_size: number
  error_message: string | null
}

export interface CreatePipePayload {
  name: string
  database: string
  sql: string
  auto_ingest: boolean
  poll_interval: number
  batch_size: string
  batch_files: number
}

// Paths are relative to the api-client base (/api/v1), which owns the prefix.
export const fetchPipes = () => api.get<Pipe[]>('/pipes')

export const fetchPipeFiles = (name: string) =>
  api.get<PipeFile[]>(`/pipes/${encodeURIComponent(name)}/files`)

export const createPipe = (payload: CreatePipePayload) =>
  api.post<Pipe>('/pipes', payload)

export const togglePipeState = ({
  name,
  action,
}: {
  name: string
  action: 'suspend' | 'resume'
}) => api.patch(`/pipes/${encodeURIComponent(name)}/${action}`)

export const deletePipe = (name: string) =>
  api.delete(`/pipes/${encodeURIComponent(name)}`)
