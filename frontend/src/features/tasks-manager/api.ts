import { api } from '@/lib/api-client'

export interface Task {
  name: string
  state: 'ACTIVE' | 'PAUSE'
  schedule: string
  database: string
  sql: string
  created_at: string
  interval?: string
}

export interface TaskRun {
  run_time: string
  finish_time: string | null
  state: 'RUNNING' | 'SUCCESS' | 'FAILED'
  error: string | null
}

export interface TasksResponse {
  tasks: Task[]
  total: number
}

// Paths are relative to the api-client base (/api/v1), which owns the prefix.
export const fetchTasks = async (): Promise<TasksResponse> =>
  api.get<TasksResponse>('/tasks')

export const fetchTaskRuns = async (name: string): Promise<TaskRun[]> => {
  const res = await api.get<{ runs?: TaskRun[] } | TaskRun[]>(
    `/tasks/${encodeURIComponent(name)}/runs`
  )
  return Array.isArray(res) ? res : (res.runs ?? [])
}

export const createTask = async (payload: Record<string, unknown>) =>
  api.post('/tasks', payload)

export const patchTaskState = async ({ name, state }: { name: string; state: string }) => {
  const action = state === 'ACTIVE' ? 'resume' : 'suspend'
  return api.patch(`/tasks/${encodeURIComponent(name)}/${action}`)
}

export const deleteTask = async (name: string) => {
  await api.delete(`/tasks/${encodeURIComponent(name)}`)
}
