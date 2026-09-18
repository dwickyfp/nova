import { api, apiBase, authHeaders } from '@/lib/api-client'

export type Stage = {
  id: string
  name: string
  database_name: string
  schema_name: string
  storage_connection: string
  base_prefix: string
  created_at: string | null
  created_by: string | null
}

export type StageListResponse = {
  stages: Stage[]
  count: number
}

export type StageFile = {
  name: string
  size: number
  last_modified: string | null
  is_dir: boolean
}

export type StageFileListResponse = {
  files: StageFile[]
  prefix: string
  count: number
}

export type StageCreatePayload = {
  name: string
  database_name: string
  schema_name: string
  storage_connection: string
  base_prefix?: string
}

export function fetchStages() {
  return api.get<StageListResponse>('/stages')
}

export function fetchStage(id: string) {
  return api.get<Stage>(`/stages/${encodeURIComponent(id)}`)
}

export function createStage(payload: StageCreatePayload) {
  return api.post<Stage>('/stages', payload)
}

export function deleteStage(id: string) {
  return api.delete<{ success: boolean; message: string }>(
    `/stages/${encodeURIComponent(id)}`
  )
}

export function fetchStageFiles(stageId: string, prefix = '') {
  const query = prefix ? `?prefix=${encodeURIComponent(prefix)}` : ''
  return api.get<StageFileListResponse>(
    `/stages/${encodeURIComponent(stageId)}/files${query}`
  )
}

export function deleteStageFile(stageId: string, filename: string) {
  const path = filename
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/')
  return api.delete<{ success: boolean; message: string }>(
    `/stages/${encodeURIComponent(stageId)}/files/${path}`
  )
}

export function uploadStageFile(stageId: string, file: File, path = '') {
  const form = new FormData()
  form.append('file', file)
  if (path) form.append('path', path)
  return api.upload<{ success: boolean; file: { filename: string; size: number } }>(
    `/stages/${encodeURIComponent(stageId)}/files`,
    form
  )
}

export function stageFileDownloadUrl(stageId: string, filename: string) {
  const path = filename
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/')
  return `${apiBase()}/stages/${encodeURIComponent(stageId)}/files/${path}`
}

export function stageFileDownloadHeaders() {
  return authHeaders()
}

export function formatBytes(bytes: number) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1
  )
  const value = bytes / 1024 ** exponent
  return `${value.toFixed(value >= 10 || exponent === 0 ? 0 : 1)} ${units[exponent]}`
}

export function queryRefFor(stage: Stage, filename: string) {
  const path = filename
    .replace(/^\/+/, '')
    .split('/')
    .join('.')
  return `@${stage.name}.${path}`
}
