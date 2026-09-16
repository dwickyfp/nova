import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchPipeFiles, fetchPipes, togglePipeState, createPipe, deletePipe } from './pipes/api'
import {
  fetchTasks,
  fetchTaskRuns,
  patchTaskState,
  createTask,
  deleteTask,
} from './tasks-manager/api'

const fetchMock = vi.fn()

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({}),
  })
})

afterEach(() => {
  fetchMock.mockReset()
  vi.unstubAllGlobals()
})

function requestedUrl(index = 0) {
  return fetchMock.mock.calls[index][0] as string
}

function requestedMethod(index = 0) {
  return fetchMock.mock.calls[index][1]?.method ?? 'GET'
}

// The client owns the /api/v1 prefix. These assertions pin the URLs the pipes
// and tasks-manager pages actually send, so a caller that repeats the prefix
// (producing /api/v1/api/v1/... which the backend 404s) fails here.
describe('pipes API calls', () => {
  it('lists pipes', async () => {
    await fetchPipes()

    expect(requestedUrl()).toBe('/api/v1/pipes')
  })

  it('lists files for a pipe', async () => {
    await fetchPipeFiles('my_pipe')

    expect(requestedUrl()).toBe('/api/v1/pipes/my_pipe/files')
  })

  it('creates a pipe', async () => {
    await createPipe({
      name: 'p',
      database: 'd',
      sql: 'INSERT INTO t SELECT 1',
      auto_ingest: true,
      poll_interval: 300,
      batch_size: '1GB',
      batch_files: 256,
    })

    expect(requestedUrl()).toBe('/api/v1/pipes')
    expect(requestedMethod()).toBe('POST')
  })

  it('suspends a pipe through the suspend endpoint', async () => {
    await togglePipeState({ name: 'my_pipe', action: 'suspend' })

    expect(requestedUrl()).toBe('/api/v1/pipes/my_pipe/suspend')
    expect(requestedMethod()).toBe('PATCH')
  })

  it('resumes a pipe through the resume endpoint', async () => {
    await togglePipeState({ name: 'my_pipe', action: 'resume' })

    expect(requestedUrl()).toBe('/api/v1/pipes/my_pipe/resume')
    expect(requestedMethod()).toBe('PATCH')
  })

  it('deletes a pipe', async () => {
    await deletePipe('my_pipe')

    expect(requestedUrl()).toBe('/api/v1/pipes/my_pipe')
    expect(requestedMethod()).toBe('DELETE')
  })
})

describe('tasks-manager API calls', () => {
  it('lists tasks', async () => {
    await fetchTasks()

    expect(requestedUrl()).toBe('/api/v1/tasks')
  })

  it('lists runs for a task', async () => {
    await fetchTaskRuns('my_task')

    expect(requestedUrl()).toBe('/api/v1/tasks/my_task/runs')
  })

  it('creates a task', async () => {
    await createTask({ name: 't' })

    expect(requestedUrl()).toBe('/api/v1/tasks')
    expect(requestedMethod()).toBe('POST')
  })

  it('resumes a task when the next state is ACTIVE', async () => {
    await patchTaskState({ name: 'my_task', state: 'ACTIVE' })

    expect(requestedUrl()).toBe('/api/v1/tasks/my_task/resume')
    expect(requestedMethod()).toBe('PATCH')
  })

  it('suspends a task when the next state is PAUSE', async () => {
    await patchTaskState({ name: 'my_task', state: 'PAUSE' })

    expect(requestedUrl()).toBe('/api/v1/tasks/my_task/suspend')
    expect(requestedMethod()).toBe('PATCH')
  })

  it('deletes a task', async () => {
    await deleteTask('my_task')

    expect(requestedUrl()).toBe('/api/v1/tasks/my_task')
    expect(requestedMethod()).toBe('DELETE')
  })
})

describe('no caller doubles the API base prefix', () => {
  it.each([
    ['fetchPipes', () => fetchPipes()],
    ['fetchPipeFiles', () => fetchPipeFiles('p')],
    ['togglePipeState', () => togglePipeState({ name: 'p', action: 'suspend' })],
    ['deletePipe', () => deletePipe('p')],
    ['fetchTasks', () => fetchTasks()],
    ['fetchTaskRuns', () => fetchTaskRuns('t')],
    ['patchTaskState', () => patchTaskState({ name: 't', state: 'ACTIVE' })],
    ['deleteTask', () => deleteTask('t')],
  ])('%s sends a single /api/v1 prefix', async (_name, call) => {
    await call()

    expect(requestedUrl().startsWith('/api/v1/')).toBe(true)
    expect(requestedUrl()).not.toContain('/api/v1/api/v1')
  })
})
