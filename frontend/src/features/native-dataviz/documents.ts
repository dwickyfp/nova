import { z } from 'zod'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import {
  chartCreateSchema, chartSchema, dashboardCreateSchema, dashboardSchema,
  deleteSchema, documentSummarySchema, eventPageSchema, grantStateSchema,
  grantsSchema, operationSchema, permissionSchema, publicationSchema,
  receiptSchema, restoreSchema, revisionSummarySchema, snapshotSchema,
  objectInspectSchema, objectInspectionSchema, objectDatasetCreateSchema,
  sqlInspectSchema, sqlInspectionSchema, sqlDatasetCreateSchema,
  objectDatasetUpdateSchema, sqlDatasetUpdateSchema,
  semanticInspectSchema, semanticInspectionSchema,
  semanticDatasetCreateSchema, semanticDatasetUpdateSchema,
  datasetPreviewSchema, datasetPreviewRequestSchema,
  dashboardDatasetCreateSchema, dashboardDatasetsSchema, type DashboardDatasetCreate,
  type ChartCreate, type ChartDefinition, type DashboardCreate, type DashboardDefinition,
  type DeleteRequest, type GrantsReplace, type Operation, type PublicationRequest,
  type RestoreRequest,
  type ObjectInspect, type ObjectDatasetCreate,
  type SqlInspect, type SqlDatasetCreate,
  type ObjectDatasetUpdate, type SqlDatasetUpdate,
  type SemanticInspect, type SemanticDatasetCreate, type SemanticDatasetUpdate,
  type DatasetPreviewRequest,
} from './contracts'

type Collection = 'charts' | 'dashboards' | 'datasets'
type RequestOptions = { signal?: AbortSignal }
type Command = { operation_id: string; expected_revision: number }
const uuid = z.uuid()
const revision = z.number().int().min(1).max(Number.MAX_SAFE_INTEGER)
const pageSize = z.number().int().min(1).max(100)

function validated<T>(schema: z.ZodType<T>, value: unknown, direction: 'request' | 'response'): T {
  const result = schema.safeParse(value)
  if (!result.success) throw new Error(`Invalid document ${direction}`)
  return result.data
}

function resourcePath(collection: Collection, id: string): string {
  if (!['charts', 'dashboards', 'datasets'].includes(collection)) throw new Error('Invalid document collection')
  return `/viz/${collection}/${validated(uuid, id, 'request').toLowerCase()}`
}

/** Both editor shells retain this epoch alongside their document state. */
export function nativeDocuments(securityEpoch: number) {
  async function send<T>(
    path: string, schema: z.ZodType<T>, options: RequestOptions = {},
    mutation?: { method: string; body: unknown; operationId?: string; expectedRevision?: number },
  ): Promise<T> {
    const controller = new AbortController()
    const verify = () => {
      if (useAuthStore.getState().securityEpoch !== securityEpoch) {
        controller.abort(new DOMException('Security context changed', 'AbortError'))
      }
      controller.signal.throwIfAborted()
      options.signal?.throwIfAborted()
    }
    const unsubscribe = useAuthStore.subscribe((state) => {
      if (state.securityEpoch !== securityEpoch) controller.abort()
    })
    try {
      verify()
      const headers: Record<string, string> = {}
      if (mutation?.operationId !== undefined) {
        headers['Idempotency-Key'] = validated(uuid, mutation.operationId, 'request')
        if (mutation.expectedRevision !== undefined) {
          headers['If-Match'] = `"${validated(revision, mutation.expectedRevision, 'request')}"`
        }
      }
      const response = await api.request<unknown>(path, {
        method: mutation?.method ?? 'GET',
        body: mutation ? JSON.stringify(mutation.body) : undefined,
        headers, securityEpoch, cache: 'no-store',
        signal: options.signal
          ? AbortSignal.any([controller.signal, options.signal])
          : controller.signal,
      })
      verify()
      return validated(schema, response, 'response')
    } finally {
      unsubscribe()
    }
  }

  async function mutate(
    path: string, method: string, body: unknown, resourceId: string,
    operationId: string, expectedRevision: number | undefined, options?: RequestOptions,
  ) {
    const receipt = await send(path, receiptSchema, options, {
      method, body, operationId: validated(uuid, operationId, 'request'), expectedRevision,
    })
    if (receipt.operation_id !== operationId.toLowerCase() || receipt.resource_id !== resourceId.toLowerCase()) {
      throw new Error('Unexpected document receipt')
    }
    return receipt
  }

  function command<T extends Command>(
    collection: Collection, id: string, suffix: string, method: string,
    schema: z.ZodType<T>, value: T, options?: RequestOptions,
  ) {
    const body = validated(schema, value, 'request')
    return mutate(resourcePath(collection, id) + suffix, method, body, id,
      body.operation_id, body.expected_revision, options)
  }

  return {
    async list(collection: Collection, workspaceId: string, options: RequestOptions & {
      draft?: boolean; limit?: number; before?: { updatedAt: string; resourceId: string }
    } = {}) {
      if (!['charts', 'dashboards', 'datasets'].includes(collection)) throw new Error('Invalid document collection')
      const query = new URLSearchParams({
        workspace_id: validated(z.string().min(1).max(128), workspaceId, 'request'),
        draft: String(options.draft ?? false),
        limit: String(validated(pageSize, options.limit ?? 50, 'request')),
      })
      if (options.before) {
        query.set('before_time', validated(z.iso.datetime({ offset: true }), options.before.updatedAt, 'request'))
        query.set('before_id', validated(uuid, options.before.resourceId, 'request'))
      }
      const items = await send(`/viz/${collection}?${query}`, documentSummarySchema.array(), options)
      const kind = collection === 'datasets' ? 'dataset' : collection === 'charts' ? 'chart' : 'dashboard'
      if (items.length > (options.limit ?? 50) || items.some(item => item.kind !== kind || item.workspace_id !== workspaceId)
        || new Set(items.map(item => item.resource_id)).size !== items.length) {
        throw new Error('Unexpected document list')
      }
      return items
    },
    async read(collection: Collection, id: string, options: RequestOptions & { draft?: boolean; revision?: number } = {}) {
      if (options.revision !== undefined && !options.draft) throw new Error('Revision reads require draft access')
      const pinned = options.revision === undefined ? '' : `&revision=${validated(revision, options.revision, 'request')}`
      const snapshot = await send(`${resourcePath(collection, id)}?draft=${options.draft ?? false}${pinned}`, snapshotSchema, options)
      const definitionId = 'chart_id' in snapshot.definition
        ? snapshot.definition.chart_id : 'dashboard_id' in snapshot.definition
          ? snapshot.definition.dashboard_id : snapshot.definition.dataset_id
      const definitionCollection = 'chart_id' in snapshot.definition
        ? 'charts' : 'dashboard_id' in snapshot.definition ? 'dashboards' : 'datasets'
      if (snapshot.resource_id !== id.toLowerCase() || definitionId !== id.toLowerCase()
        || (options.revision !== undefined && snapshot.revision !== options.revision)
        || collection !== definitionCollection
        || (definitionCollection === 'datasets'
          && ('definition_scope' in snapshot.definition) === Boolean(options.draft))) {
        throw new Error('Unexpected document response')
      }
      return snapshot
    },
    async inspectSource(value: ObjectInspect, options?: RequestOptions) {
      const body = validated(objectInspectSchema, value, 'request')
      const result = await send('/viz/datasets/inspect', objectInspectionSchema, options, {
        method: 'POST', body,
      })
      if (result.dataset_id !== body.dataset_id.toLowerCase()
        || result.source.kind !== body.source.kind
        || result.source.catalog !== (body.source.catalog ?? 'default_catalog')
        || result.source.database !== body.source.database
        || result.source.schema_name !== (body.source.schema_name ?? 'default')
        || result.source.object_name !== body.source.object_name) {
        throw new Error('Unexpected dataset inspection')
      }
      return result
    },
    createDataset(value: ObjectDatasetCreate, operationId: string, options?: RequestOptions) {
      const body = validated(objectDatasetCreateSchema, value, 'request')
      return mutate('/viz/datasets', 'POST', body, body.dataset_id, operationId, undefined, options)
    },
    createDashboardDataset(id: string, value: DashboardDatasetCreate, intent: Command, options?: RequestOptions) {
      const body = validated(dashboardDatasetCreateSchema, value, 'request')
      return mutate(`${resourcePath('dashboards', id)}/datasets`, 'POST', body, id,
        intent.operation_id, intent.expected_revision, options)
    },
    async dashboardDatasets(id: string, pinnedRevision: number, options?: RequestOptions) {
      const result = await send(`${resourcePath('dashboards', id)}/datasets?revision=${validated(revision, pinnedRevision, 'request')}`,
        dashboardDatasetsSchema, options)
      if (result.dashboard_id !== id.toLowerCase() || result.revision !== pinnedRevision
        || new Set(result.datasets.map(item => item.dataset_id)).size !== result.datasets.length) {
        throw new Error('Unexpected dashboard datasets')
      }
      return result
    },
    async inspectSql(value: SqlInspect, options?: RequestOptions) {
      const body = validated(sqlInspectSchema, value, 'request')
      const result = await send('/viz/datasets/inspect', sqlInspectionSchema, options, {
        method: 'POST', body,
      })
      if (result.dataset_id !== body.dataset_id.toLowerCase()
        || result.source.sql !== body.source.sql
        || result.source.catalog !== (body.source.catalog ?? 'default_catalog')
        || result.source.database !== body.source.database
        || result.source.schema_name !== (body.source.schema_name ?? 'default')
        || JSON.stringify(result.parameters ?? []) !== JSON.stringify(body.parameters ?? [])) {
        throw new Error('Unexpected dataset inspection')
      }
      return result
    },
    createSqlDataset(value: SqlDatasetCreate, operationId: string, options?: RequestOptions) {
      const body = validated(sqlDatasetCreateSchema, value, 'request')
      return mutate('/viz/datasets', 'POST', body, body.dataset_id, operationId, undefined, options)
    },
    async inspectSemantic(value: SemanticInspect, options?: RequestOptions) {
      const body = validated(semanticInspectSchema, value, 'request')
      const result = await send('/viz/datasets/inspect', semanticInspectionSchema, options, {
        method: 'POST', body,
      })
      if (result.dataset_id !== body.dataset_id.toLowerCase()
        || result.source.model_id !== body.source.model_id
        || result.source.model_revision !== body.source.model_revision
        || result.source.model_fingerprint !== body.source.model_fingerprint
        || JSON.stringify(result.source.dimensions) !== JSON.stringify(body.source.dimensions ?? [])
        || JSON.stringify(result.source.metrics) !== JSON.stringify(body.source.metrics)
        || JSON.stringify(result.source.named_filters) !== JSON.stringify(body.source.named_filters ?? [])) {
        throw new Error('Unexpected dataset inspection')
      }
      return result
    },
    createSemanticDataset(value: SemanticDatasetCreate, operationId: string, options?: RequestOptions) {
      const body = validated(semanticDatasetCreateSchema, value, 'request')
      return mutate('/viz/datasets', 'POST', body, body.dataset_id, operationId, undefined, options)
    },
    updateSemanticDataset(value: SemanticDatasetUpdate, intent: Command, options?: RequestOptions) {
      const body = validated(semanticDatasetUpdateSchema, value, 'request')
      return mutate(`${resourcePath('datasets', body.dataset_id)}/draft`, 'PATCH', body,
        body.dataset_id, intent.operation_id, intent.expected_revision, options)
    },
    updateDataset(value: ObjectDatasetUpdate, intent: Command, options?: RequestOptions) {
      const body = validated(objectDatasetUpdateSchema, value, 'request')
      return mutate(`${resourcePath('datasets', body.dataset_id)}/draft`, 'PATCH', body,
        body.dataset_id, intent.operation_id, intent.expected_revision, options)
    },
    updateSqlDataset(value: SqlDatasetUpdate, intent: Command, options?: RequestOptions) {
      const body = validated(sqlDatasetUpdateSchema, value, 'request')
      return mutate(`${resourcePath('datasets', body.dataset_id)}/draft`, 'PATCH', body,
        body.dataset_id, intent.operation_id, intent.expected_revision, options)
    },
    async previewDataset(id: string, value: DatasetPreviewRequest, options?: RequestOptions) {
      const body = validated(datasetPreviewRequestSchema, value, 'request')
      const result = await send(`${resourcePath('datasets', id)}/preview`, datasetPreviewSchema, options, {
        method: 'POST', body,
      })
      const limit = body.limit ?? 100
      const offset = body.offset ?? 0
      const next = offset + result.rows.length
      const expectedNext = result.has_more && next <= 10_000 ? next : null
      if (result.dataset_id !== id.toLowerCase() || result.revision !== body.revision
        || result.offset !== offset || result.rows.length > limit
        || (result.has_more && result.rows.length !== limit) || result.next_offset !== expectedNext
        || result.rows.some(row => row.length !== result.fields.length || row.some((cell, i) =>
          cell === null ? !result.fields[i].nullable : cell.type !== result.fields[i].logical_type))) {
        throw new Error('Unexpected dataset preview')
      }
      return result
    },
    createChart(value: ChartCreate, operationId: string, options?: RequestOptions) {
      const body = validated(chartCreateSchema, value, 'request')
      return mutate('/viz/charts', 'POST', body, body.definition.chart_id, operationId, undefined, options)
    },
    createDashboard(value: DashboardCreate, operationId: string, options?: RequestOptions) {
      const body = validated(dashboardCreateSchema, value, 'request')
      return mutate('/viz/dashboards', 'POST', body, body.definition.dashboard_id, operationId, undefined, options)
    },
    replaceChart(value: ChartDefinition, intent: Command, options?: RequestOptions) {
      const body = validated(chartSchema, value, 'request')
      return mutate(`${resourcePath('charts', body.chart_id)}/draft`, 'PATCH', body,
        body.chart_id, intent.operation_id, intent.expected_revision, options)
    },
    replaceDashboard(value: DashboardDefinition, intent: Command, options?: RequestOptions) {
      const body = validated(dashboardSchema, value, 'request')
      return mutate(`${resourcePath('dashboards', body.dashboard_id)}/draft`, 'PATCH', body,
        body.dashboard_id, intent.operation_id, intent.expected_revision, options)
    },
    apply(id: string, body: Operation, options?: RequestOptions) {
      return command('dashboards', id, '/operations', 'POST', operationSchema, body, options)
    },
    publish(collection: Collection, id: string, body: PublicationRequest, options?: RequestOptions) {
      return command(collection, id, '/publish', 'POST', publicationSchema, body, options)
    },
    restore(collection: Collection, id: string, body: RestoreRequest, options?: RequestOptions) {
      return command(collection, id, '/restore', 'POST', restoreSchema, body, options)
    },
    remove(collection: Collection, id: string, body: DeleteRequest, options?: RequestOptions) {
      return command(collection, id, '', 'DELETE', deleteSchema, body, options)
    },
    grants(collection: Collection, id: string, options?: RequestOptions) {
      return send(`${resourcePath(collection, id)}/grants`, grantStateSchema, options)
    },
    replaceGrants(collection: Collection, id: string, body: GrantsReplace, options?: RequestOptions) {
      return command(collection, id, '/grants', 'PUT', grantsSchema, body, options)
    },
    permissions(collection: Collection, id: string, options?: RequestOptions) {
      return send(`${resourcePath(collection, id)}/permissions`, permissionSchema.array(), options)
    },
    history(collection: Collection, id: string, options: RequestOptions & { before?: number; limit?: number } = {}) {
      const query = new URLSearchParams({ limit: String(validated(pageSize, options.limit ?? 50, 'request')) })
      if (options.before !== undefined) query.set('before', String(validated(revision, options.before, 'request')))
      return send(`${resourcePath(collection, id)}/revisions?${query}`, revisionSummarySchema.array(), options)
    },
    events(collection: Collection, id: string, options: RequestOptions & { after?: number; limit?: number } = {}) {
      const query = new URLSearchParams({
        after: String(validated(z.number().int().min(0).max(Number.MAX_SAFE_INTEGER), options.after ?? 0, 'request')),
        limit: String(validated(pageSize, options.limit ?? 100, 'request')),
      })
      return send(`${resourcePath(collection, id)}/events?${query}`, eventPageSchema, options)
    },
    async receipt(operationId: string, options?: RequestOptions) {
      const id = validated(uuid, operationId, 'request').toLowerCase()
      const receipt = await send(`/viz/operations/${id}`, receiptSchema, options)
      if (receipt.operation_id !== id) throw new Error('Unexpected document receipt')
      return receipt
    },
  }
}
