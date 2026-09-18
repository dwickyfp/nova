import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createStage,
  deleteStage,
  deleteStageFile,
  fetchStage,
  fetchStageFiles,
  fetchStages,
  queryRefFor,
  stageFileDownloadUrl,
  uploadStageFile,
  type Stage,
} from './api'

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

const stage: Stage = {
  id: 'stage-uuid',
  name: 'stage1',
  database_name: 'DATALAKE',
  schema_name: 'bronze',
  storage_connection: 'production',
  base_prefix: 'datalake/bronze/stage1',
  created_at: null,
  created_by: null,
}

describe('stage manager API calls', () => {
  it('lists stages without a doubled API prefix', async () => {
    await fetchStages()

    expect(requestedUrl()).toBe('/api/v1/stages')
    expect(requestedUrl()).not.toContain('/api/v1/api/v1')
  })

  it('fetches a single stage by id', async () => {
    await fetchStage('stage-uuid')

    expect(requestedUrl()).toBe('/api/v1/stages/stage-uuid')
  })

  it('creates a stage', async () => {
    await createStage({
      name: 'stage1',
      database_name: 'DATALAKE',
      schema_name: 'bronze',
      storage_connection: 'production',
    })

    expect(requestedUrl()).toBe('/api/v1/stages')
    expect(requestedMethod()).toBe('POST')
  })

  it('deletes a stage', async () => {
    await deleteStage('stage-uuid')

    expect(requestedUrl()).toBe('/api/v1/stages/stage-uuid')
    expect(requestedMethod()).toBe('DELETE')
  })

  it('lists files under a prefix', async () => {
    await fetchStageFiles('stage-uuid', 'data')

    expect(requestedUrl()).toBe('/api/v1/stages/stage-uuid/files?prefix=data')
  })

  it('omits the prefix parameter when browsing the root', async () => {
    await fetchStageFiles('stage-uuid')

    expect(requestedUrl()).toBe('/api/v1/stages/stage-uuid/files')
  })

  it('encodes each path segment when deleting a file', async () => {
    await deleteStageFile('stage-uuid', 'data/orders 2024.parquet')

    expect(requestedUrl()).toBe(
      '/api/v1/stages/stage-uuid/files/data/orders%202024.parquet'
    )
    expect(requestedMethod()).toBe('DELETE')
  })

  it('uploads through the multipart endpoint without a JSON content type', async () => {
    const file = new File(['a,b'], 'data.csv', { type: 'text/csv' })

    await uploadStageFile('stage-uuid', file)

    expect(requestedUrl()).toBe('/api/v1/stages/stage-uuid/files')
    expect(requestedMethod()).toBe('POST')
    expect(fetchMock.mock.calls[0][1]?.body).toBeInstanceOf(FormData)
  })
})

describe('stage query reference', () => {
  it('renders the @stage.file syntax from a nested path', () => {
    expect(queryRefFor(stage, 'data/orders.parquet')).toBe(
      '@stage1.data.orders.parquet'
    )
  })

  it('keeps a root file at the stage root', () => {
    expect(queryRefFor(stage, 'data.csv')).toBe('@stage1.data.csv')
  })
})

describe('stage file download URL', () => {
  it('builds a URL the browser can open directly', () => {
    expect(stageFileDownloadUrl('stage-uuid', 'data/report.csv')).toBe(
      '/api/v1/stages/stage-uuid/files/data/report.csv'
    )
  })
})
