import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { nativeDocuments } from './documents'

const id = (n: number) => `aaaaaaaa-0000-4000-8000-${String(n).padStart(12, '0')}`
const document = { dashboard_id: id(1), title: 'Sales', pages: [{ page_id: id(2), title: 'Overview' }] }
const receipt = {
  operation_id: id(3), resource_id: id(1), base_revision: 1,
  new_revision: 2, event_sequence: 2, committed_at: '2026-11-13T00:00:00Z',
}
const intent = { operation_id: id(3), expected_revision: 1 }
const fetchMock = vi.fn()
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
const client = () => nativeDocuments(useAuthStore.getState().securityEpoch)

beforeEach(() => {
  useAuthStore.getState().auth.setAccessToken('document-test-token')
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValue(response(receipt))
})
afterEach(() => {
  vi.unstubAllGlobals()
  fetchMock.mockReset()
  useAuthStore.getState().auth.reset()
})

describe('native document transport', () => {
  it('loads a bounded dataset index with exact dashboard identity and revision', async () => {
    const index = { dashboard_id: id(1), revision: 2,
      datasets: [{ dataset_id: id(4), revision: 1, title: null }] }
    fetchMock.mockResolvedValueOnce(response(index))
    await expect(client().dashboardDatasets(id(1), 2)).resolves.toEqual(index)
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/v1/viz/dashboards/${id(1)}/datasets?revision=2`)
    for (const wrong of [{ dashboard_id: id(2) }, { revision: 3 }, { datasets: [...index.datasets, ...index.datasets] }]) {
      fetchMock.mockResolvedValueOnce(response({ ...index, ...wrong }))
      await expect(client().dashboardDatasets(id(1), 2)).rejects.toThrow('Unexpected dashboard datasets')
    }
  })

  it('reads an immutable revision only in the authorized draft scope', async () => {
    const snapshot = { resource_id: id(1), revision: 2, metadata_revision: 6,
      grants_revision: 1, publication_id: null, definition: { ...document,
        schema_version: 2, columns: 12, description: '', datasets: [], filters: [], actions: [],
        pages: document.pages.map(item => ({ ...item, widgets: [] })),
      } }
    fetchMock.mockResolvedValueOnce(response(snapshot))
    await expect(client().read('dashboards', id(1), { draft: true, revision: 2 })).resolves.toEqual(snapshot)
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/v1/viz/dashboards/${id(1)}?draft=true&revision=2`)
    await expect(client().read('dashboards', id(1), { revision: 2 })).rejects.toThrow('Revision reads require draft access')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    fetchMock.mockResolvedValueOnce(response({ ...snapshot, revision: 3 }))
    await expect(client().read('dashboards', id(1), { draft: true, revision: 2 })).rejects.toThrow('Unexpected document response')
  })

  const selection = { kind: 'table' as const, database: 'analytics', object_name: 'sales' }
  const inspected = {
    dataset_id: id(1),
    source: { ...selection, catalog: 'default_catalog', schema_name: 'default', physical_id: '1061' },
    fields: [{ field_id: id(4), name: 'amount', source_column: 'amount', logical_type: 'decimal',
      physical_type: 'decimal(38,12)', semantic_role: 'measure', nullable: true, timezone: null, unit: null, currency: null }],
    schema_fingerprint: 'a'.repeat(64),
  }

  const preview = { dataset_id: id(1), revision: 1, fields: inspected.fields,
    rows: [[{ type: 'decimal', value: '99999999999999999999999999.123456789012' }], [null]],
    offset: 0, has_more: true, next_offset: 2 }

  const semantic = { kind: 'semantic_model' as const, model_id: id(8), model_revision: 3,
    model_fingerprint: 'c'.repeat(64), dimensions: [], metrics: ['amount'], named_filters: [] }
  const semanticInspection = { ...inspected, source: { ...semantic,
    database: 'analytics', catalog: 'default_catalog', schema_name: 'default',
    dependencies: [inspected.source] } }

  it('creates and attaches a dataset with one dashboard revision and receipt', async () => {
    const dataset = { dataset_id: id(4), title: 'Sales data', source: selection,
      expected_physical_id: '1061', expected_schema_fingerprint: 'a'.repeat(64) }
    await expect(client().createDashboardDataset(id(1), { dataset }, intent)).resolves.toEqual(receipt)
    const [path, options] = fetchMock.mock.calls[0]
    expect(path).toContain(`/viz/dashboards/${id(1)}/datasets`)
    expect(options.headers['If-Match']).toBe('"1"')
    expect(options.headers['Idempotency-Key']).toBe(id(3))
    expect(JSON.parse(options.body)).toMatchObject({ dataset })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('refuses a dataset receipt for a dashboard creation command', async () => {
    fetchMock.mockResolvedValueOnce(response({ ...receipt, resource_id: id(4) }))
    await expect(client().createDashboardDataset(id(1), { dataset: {
      dataset_id: id(4), title: 'Sales data', source: semantic,
      expected_schema_fingerprint: 'a'.repeat(64),
    } }, intent)).rejects.toThrow('Unexpected document receipt')
  })

  it('refuses a client workspace override in dashboard dataset creation', async () => {
    const value = { dataset: { dataset_id: id(4), title: 'Sales data', source: semantic,
      expected_schema_fingerprint: 'a'.repeat(64), workspace_id: 'other-workspace' } }
    await expect(async () => client().createDashboardDataset(id(1), value, intent))
      .rejects.toThrow('Invalid document request')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('retains the exact semantic version and typed selection', async () => {
    fetchMock.mockResolvedValueOnce(response(semanticInspection))
    const result = await client().inspectSemantic({ dataset_id: id(1), source: semantic })
    expect(result.source.model_revision).toBe(3)
    expect(result.source).not.toHaveProperty('sql')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).source).toEqual(semantic)
  })

  it.each([
    { model_revision: 4 }, { model_fingerprint: 'd'.repeat(64) },
    { model_id: id(9) }, { metrics: ['other'] }, { dimensions: ['region'] },
    { named_filters: ['paid'] },
  ])('rejects a semantic inspection with changed pins or selection: %j', async (change) => {
    fetchMock.mockResolvedValueOnce(response({ ...semanticInspection,
      source: { ...semanticInspection.source, ...change } }))
    await expect(client().inspectSemantic({ dataset_id: id(1), source: semantic }))
      .rejects.toThrow('Unexpected dataset inspection')
  })

  it('creates and edits semantic datasets through the shared receipt boundary', async () => {
    const body = { dataset_id: id(1), source: semantic, title: 'Sales',
      expected_schema_fingerprint: inspected.schema_fingerprint }
    await expect(client().createSemanticDataset({ ...body, workspace_id: 'personal' }, id(3)))
      .resolves.toEqual(receipt)
    fetchMock.mockResolvedValueOnce(response(receipt))
    await expect(client().updateSemanticDataset(body, intent)).resolves.toEqual(receipt)
    expect(fetchMock.mock.calls[1][0]).toBe(`/api/v1/viz/datasets/${id(1)}/draft`)
    expect(fetchMock.mock.calls[1][1].headers['If-Match']).toBe('"1"')
    expect(fetchMock.mock.calls[1][1].headers['Idempotency-Key']).toBe(id(3))
  })

  it('accepts dataset publications without exposing a source definition', async () => {
    const publicDefinition = { dataset_id: id(1), title: 'Sales', schema_version: 2,
      definition_scope: 'publication', source_kind: 'sql', fields: inspected.fields,
      parameters: [], metrics: [], grain: [] }
    fetchMock.mockResolvedValueOnce(response({ resource_id: id(1), revision: 1,
      metadata_revision: 1, grants_revision: 1, publication_id: id(5), definition: publicDefinition }))
    const result = await client().read('datasets', id(1))
    expect(result.definition).not.toHaveProperty('source')
    expect(result.definition).not.toHaveProperty('schema_fingerprint')
  })

  it('rejects a full dataset definition returned on a publication read', async () => {
    fetchMock.mockResolvedValueOnce(response({ resource_id: id(1), revision: 1,
      metadata_revision: 1, grants_revision: 1, publication_id: id(5),
      definition: { ...inspected, title: 'Sales' } }))
    await expect(client().read('datasets', id(1))).rejects.toThrow('Unexpected document response')
  })

  it('updates a reviewed source using the same revision and receipt boundary', async () => {
    const body = { dataset_id: id(1), title: 'Sales', source: selection,
      expected_physical_id: '1061', expected_schema_fingerprint: 'a'.repeat(64) }
    await expect(client().updateDataset(body, intent)).resolves.toEqual(receipt)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`/api/v1/viz/datasets/${id(1)}/draft`)
    expect(options.method).toBe('PATCH')
    expect(options.headers['Idempotency-Key']).toBe(id(3))
    expect(options.headers['If-Match']).toBe('"1"')
    expect(JSON.parse(options.body)).not.toHaveProperty('fields')
  })

  it('keeps source edit conflicts visible to the caller', async () => {
    fetchMock.mockResolvedValueOnce(response({ detail: 'Revision changed' }, 409))
    await expect(client().updateSqlDataset({ dataset_id: id(1), title: 'Sales',
      source: { kind: 'sql', database: 'analytics', sql: 'SELECT amount FROM sales' },
      expected_schema_fingerprint: 'a'.repeat(64) }, intent)).rejects.toMatchObject({ status: 409 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('previews a pinned revision without rounding decimals or inventing a total', async () => {
    fetchMock.mockResolvedValueOnce(response(preview))
    const result = await client().previewDataset(id(1), { revision: 1, limit: 2 })
    expect(result).toEqual(preview)
    expect(result).not.toHaveProperty('total')
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/v1/viz/datasets/${id(1)}/preview`)
    expect(fetchMock.mock.calls[0][1].headers['Idempotency-Key']).toBeUndefined()
  })

  it.each([
    { ...preview, revision: 2 }, { ...preview, dataset_id: id(2) },
    { ...preview, offset: 3 }, { ...preview, next_offset: 9 },
    { ...preview, rows: [[{ type: 'string', value: 'private-cell' }], [null]] },
    { ...preview, rows: [[null, null], [null]] },
    { ...preview, rows: [[null]] },
  ])('discards mismatched preview replies', async body => {
    fetchMock.mockResolvedValueOnce(response(body))
    await expect(client().previewDataset(id(1), { revision: 1, limit: 2 }))
      .rejects.toThrow('Unexpected dataset preview')
  })

  it('inspects a source without issuing a document mutation', async () => {
    fetchMock.mockResolvedValueOnce(response(inspected))
    const result = await client().inspectSource({ dataset_id: id(1), source: selection })
    expect(result.source.physical_id).toBe('1061')
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/viz/datasets/inspect')
    expect(options.method).toBe('POST')
    expect(options.headers['Idempotency-Key']).toBeUndefined()
    expect(options.cache).toBe('no-store')
  })

  it.each([
    { ...inspected, dataset_id: id(9) },
    { ...inspected, source: { ...inspected.source, object_name: 'another_table' } },
  ])('discards an inspection belonging to another request', async (value) => {
    fetchMock.mockResolvedValueOnce(response(value))
    await expect(client().inspectSource({ dataset_id: id(1), source: selection }))
      .rejects.toThrow('Unexpected dataset inspection')
  })

  it('saves a dataset using the observed identity without submitting an authoritative schema', async () => {
    const body = { dataset_id: id(1), source: selection, title: 'Sales', workspace_id: 'personal',
      expected_physical_id: '1061', expected_schema_fingerprint: 'a'.repeat(64) }
    await expect(client().createDataset(body, id(3))).resolves.toEqual(receipt)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/viz/datasets')
    expect(options.headers['Idempotency-Key']).toBe(id(3))
    expect(JSON.parse(options.body)).not.toHaveProperty('fields')
    expect(JSON.parse(options.body)).toMatchObject(body)
  })

  it('reads dataset revisions and refuses a dataset on a chart path', async () => {
    const snapshot = { resource_id: id(1), revision: 1, metadata_revision: 1, grants_revision: 1,
      publication_id: null, definition: { ...inspected, title: 'Sales' } }
    fetchMock.mockResolvedValueOnce(response(snapshot))
    await expect(client().read('datasets', id(1), { draft: true })).resolves.toMatchObject(snapshot)
    fetchMock.mockResolvedValueOnce(response(snapshot))
    await expect(client().read('charts', id(1), { draft: true })).rejects.toThrow('Unexpected document response')
  })

  it('inspects SQL through the same authenticated dataset transport', async () => {
    const source = { kind: 'sql' as const, database: 'analytics', sql: 'SELECT id FROM sales' }
    const result = { ...inspected, source: { ...source, catalog: 'default_catalog', schema_name: 'default',
      ast_digest: 'b'.repeat(64), dependencies: [inspected.source] } }
    fetchMock.mockResolvedValueOnce(response(result))
    // The contract schema supplies the declared default for an omitted parameter list.
    await expect(client().inspectSql({ dataset_id: id(1), source })).resolves.toEqual({ ...result, parameters: [] })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/viz/datasets/inspect')
    expect(fetchMock.mock.calls[0][1].headers['Idempotency-Key']).toBeUndefined()
  })

  it('rejects SQL inspection from a different statement', async () => {
    const source = { kind: 'sql' as const, database: 'analytics', sql: 'SELECT id FROM sales' }
    fetchMock.mockResolvedValueOnce(response({ ...inspected, source: { ...source, sql: 'SELECT id FROM other',
      catalog: 'default_catalog', schema_name: 'default', ast_digest: 'b'.repeat(64), dependencies: [inspected.source] } }))
    await expect(client().inspectSql({ dataset_id: id(1), source })).rejects.toThrow('Unexpected dataset inspection')
  })

  it('creates SQL datasets with a verified fingerprint and a durable operation identity', async () => {
    await expect(client().createSqlDataset({ dataset_id: id(1), title: 'Sales', workspace_id: 'personal',
      expected_schema_fingerprint: 'a'.repeat(64),
      source: { kind: 'sql', database: 'analytics', sql: 'SELECT id FROM sales' },
    }, id(3))).resolves.toEqual(receipt)
    expect(fetchMock.mock.calls[0][1].headers['Idempotency-Key']).toBe(id(3))
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).not.toHaveProperty('fields')
  })

  it('matches canonical UUID receipts for an uppercase request identity', async () => {
    await expect(client().replaceDashboard(
      { ...document, dashboard_id: id(1).toUpperCase() },
      { ...intent, operation_id: id(3).toUpperCase() },
    )).resolves.toEqual(receipt)
    expect(fetchMock.mock.calls[0][0]).toContain(id(1))
  })

  it('uses the shared authenticated client and strong revision preconditions', async () => {
    await expect(client().replaceDashboard(document, intent)).resolves.toEqual(receipt)
    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`/api/v1/viz/dashboards/${id(1)}/draft`)
    expect(options).toMatchObject({ method: 'PATCH', cache: 'no-store', headers: {
      Authorization: 'Bearer document-test-token', 'Idempotency-Key': id(3), 'If-Match': '"1"',
    } })
    expect(options.securityEpoch).toBeUndefined()
    expect(JSON.parse(options.body)).toMatchObject(document)
  })

  it('does not generate a new operation or replay a mutation after an unknown outcome', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Connection lost'))
    await expect(client().replaceDashboard(document, intent)).rejects.toThrow('Connection lost')
    expect(fetchMock).toHaveBeenCalledOnce()
    fetchMock.mockResolvedValueOnce(response(receipt))
    await expect(client().receipt(intent.operation_id)).resolves.toEqual(receipt)
    expect(fetchMock.mock.calls[1][0]).toBe(`/api/v1/viz/operations/${id(3)}`)
  })

  it.each([403, 409, 503])('preserves HTTP %s for refusal, conflict and maintenance states', async (status) => {
    fetchMock.mockResolvedValueOnce(response({ detail: 'Change refused' }, status))
    await expect(client().replaceDashboard(document, intent)).rejects.toMatchObject({ status })
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('refuses mismatched receipts instead of reporting a saved revision', async () => {
    fetchMock.mockResolvedValueOnce(response({ ...receipt, resource_id: id(9) }))
    await expect(client().replaceDashboard(document, intent)).rejects.toThrow('Unexpected document receipt')
  })

  it('rejects private response fields without including their values in validation errors', async () => {
    fetchMock.mockResolvedValueOnce(response({ ...receipt, password: 'private-canary' }))
    const error = await client().receipt(id(3)).catch((error: unknown) => error)
    expect(error).toBeInstanceOf(Error)
    expect(String(error)).toBe('Error: Invalid document response')
    expect(String(error)).not.toContain('private-canary')
  })

  it.each([0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])('rejects invalid CAS revision %s before I/O', async (expected_revision) => {
    await expect(client().replaceDashboard(document, { ...intent, expected_revision })).rejects.toThrow('Invalid document request')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('keeps workspace names and cursor timestamps out of the URL structure', async () => {
    fetchMock.mockResolvedValueOnce(response([]))
    await client().list('dashboards', 'team&draft=true', {
      before: { updatedAt: '2026-11-13T07:00:00+07:00', resourceId: id(1) },
    })
    const url = new URL(fetchMock.mock.calls[0][0], 'http://localhost')
    expect(url.searchParams.get('workspace_id')).toBe('team&draft=true')
    expect(url.searchParams.getAll('draft')).toEqual(['false'])
    expect(url.searchParams.get('before_time')).toBe('2026-11-13T07:00:00+07:00')
  })

  it.each(['workspace', 'kind', 'duplicate', 'limit'])('rejects a document list with an invalid %s boundary', async boundary => {
    const item = { resource_id: id(2), kind: 'dataset', title: 'Private dataset',
      owner_name: 'alice', workspace_id: 'personal:alice', updated_at: '2026-11-14T00:00:00Z',
      metadata_revision: 1, grants_revision: 1, current_publication_id: null, revision: 1 }
    const items = boundary === 'workspace' ? [{ ...item, workspace_id: 'private:someone' }]
      : boundary === 'kind' ? [{ ...item, kind: 'chart' }]
        : boundary === 'duplicate' ? [item, item] : [item, { ...item, resource_id: id(3) }]
    fetchMock.mockResolvedValueOnce(response(items))
    await expect(client().list('datasets', 'personal:alice', { limit: boundary === 'limit' ? 1 : 50 }))
      .rejects.toThrow('Unexpected document list')
  })

  it('reads events from sequence zero and uses the grant revision for permission changes', async () => {
    fetchMock.mockResolvedValueOnce(response({ events: [], through: 1, next_sequence: 0 }))
    await client().events('dashboards', id(1))
    expect(fetchMock.mock.calls[0][0]).toContain('after=0&limit=100')
    await client().replaceGrants('dashboards', id(1), { ...intent, expected_revision: 7, grants: [] })
    expect(fetchMock.mock.calls[1][1].headers['If-Match']).toBe('"7"')
  })

  it('rejects a response for a different document or collection', async () => {
    fetchMock.mockResolvedValueOnce(response({
      resource_id: id(1), revision: 1, metadata_revision: 1, grants_revision: 1,
      publication_id: null, definition: { ...document, dashboard_id: id(9) },
    }))
    await expect(client().read('dashboards', id(1))).rejects.toThrow('Unexpected document response')
  })

  it('uses the original operation for deletion and receipt verification after deletion', async () => {
    await client().remove('dashboards', id(1), intent)
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(intent)
    fetchMock.mockResolvedValueOnce(response(receipt))
    await expect(client().receipt(id(3))).resolves.toEqual(receipt)
    expect(fetchMock.mock.calls[1][0]).toBe(`/api/v1/viz/operations/${id(3)}`)
  })
})

describe('document identity and cancellation', () => {
  it('releases its identity subscription after a request settles', async () => {
    await client().receipt(id(3))
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal
    useAuthStore.getState().auth.reset()
    expect(signal.aborted).toBe(false)
  })

  it('does not submit an edit retained from an earlier role', async () => {
    const documents = client()
    useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['VIEWER'], activeRole: 'VIEWER' })
    await expect(documents.replaceDashboard(document, intent)).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('aborts in-flight requests and discards responses after a role switch', async () => {
    let finish!: (value: Response) => void
    fetchMock.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve }))
    const pending = client().receipt(id(3))
    const checked = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['VIEWER'], activeRole: 'VIEWER' })
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
    finish(response(receipt))
    await checked
  })

  it('does not return data if the role changes during response body decoding', async () => {
    let finish!: (value: unknown) => void
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200,
      headers: new Headers({ 'Content-Type': 'application/json' }),
      json: () => new Promise((resolve) => { finish = resolve }),
    })
    const pending = client().receipt(id(3))
    const checked = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    await vi.waitFor(() => expect(finish).toBeTypeOf('function'))
    useAuthStore.getState().auth.reset()
    finish(receipt)
    await checked
  })

  it('propagates cancellation without sending or retrying a command', async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(client().replaceDashboard(document, intent, { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('does not replay an old-epoch mutation after token renewal', async () => {
    fetchMock.mockResolvedValueOnce(response({}, 401))
    fetchMock.mockResolvedValueOnce(response({ access_token: 'renewed-document-test-token' }))
    await expect(client().replaceDashboard(document, intent)).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `/api/v1/viz/dashboards/${id(1)}/draft`, '/api/v1/auth/renew',
    ])
    expect(useAuthStore.getState().auth.accessToken).toBe('renewed-document-test-token')
  })

  it('preserves structured API errors for the editor', async () => {
    fetchMock.mockResolvedValueOnce(response({ detail: 'Read only' }, 403))
    await expect(client().receipt(id(3))).rejects.toBeInstanceOf(ApiError)
  })
})
