import { z } from 'zod'
import schema from './schema'
import type {
  ChartDefinition,
  DashboardDefinition,
  DatasetDefinition,
  DatasetPreview,
  DatasetPreviewRequest,
  DatasetQueryRequest,
  DatasetQueryResult,
  DashboardDatasetCreate,
  DashboardDatasets,
  ChartCreate,
  DashboardCreate,
  DeleteRequest,
  DocumentSnapshot,
  DocumentSummary,
  EventPage,
  GrantState,
  GrantSummary,
  GrantsReplace,
  Operation,
  OperationReceipt,
  ObjectInspect,
  ObjectInspection,
  ObjectDatasetCreate,
  ObjectDatasetUpdate,
  Parameter,
  ParameterInput,
  SqlInspect,
  SqlInspection,
  SqlDatasetCreate,
  SqlDatasetUpdate,
  SemanticInspect,
  SemanticInspection,
  SemanticDatasetCreate,
  SemanticDatasetUpdate,
  PublicationRequest,
  RestoreRequest,
  RevisionSummary,
} from './types'

export type * from './types'

function contract<T>(name: keyof typeof schema.$defs): z.ZodType<T> {
  return z.fromJSONSchema({
    $schema: 'https://json-schema.org/draft/2020-12/schema',
    $ref: `#/$defs/${name}`,
    $defs: schema.$defs as Record<string, z.core.JSONSchema.JSONSchema>,
  }) as z.ZodType<T>
}

// These validate wire shape. Cross-resource, layout and authorization checks
// remain server-side and must succeed before any edit is reported as saved.
export const datasetSchema = contract<DatasetDefinition>('DatasetDefinition')
export const datasetPreviewSchema = contract<DatasetPreview>('DatasetPreview')
export const datasetPreviewRequestSchema = contract<DatasetPreviewRequest>('DatasetPreviewRequest')
export const datasetQueryRequestSchema = contract<DatasetQueryRequest>('DatasetQueryRequest')
export const datasetQueryResultSchema = contract<DatasetQueryResult>('DatasetQueryResult')
export const parameterSchema = contract<Parameter>('Parameter')
export const parameterInputSchema = contract<ParameterInput>('ParameterInput')
export const dashboardDatasetCreateSchema = contract<DashboardDatasetCreate>('DashboardDatasetCreate')
export const dashboardDatasetsSchema = contract<DashboardDatasets>('DashboardDatasets')
export const objectInspectSchema = contract<ObjectInspect>('ObjectInspect')
export const objectInspectionSchema = contract<ObjectInspection>('ObjectInspection')
export const objectDatasetCreateSchema = contract<ObjectDatasetCreate>('ObjectDatasetCreate')
export const objectDatasetUpdateSchema = contract<ObjectDatasetUpdate>('ObjectDatasetUpdate')
export const sqlInspectSchema = contract<SqlInspect>('SqlInspect')
export const sqlInspectionSchema = contract<SqlInspection>('SqlInspection')
export const sqlDatasetCreateSchema = contract<SqlDatasetCreate>('SqlDatasetCreate')
export const sqlDatasetUpdateSchema = contract<SqlDatasetUpdate>('SqlDatasetUpdate')
export const semanticInspectSchema = contract<SemanticInspect>('SemanticInspect')
export const semanticInspectionSchema = contract<SemanticInspection>('SemanticInspection')
export const semanticDatasetCreateSchema = contract<SemanticDatasetCreate>('SemanticDatasetCreate')
export const semanticDatasetUpdateSchema = contract<SemanticDatasetUpdate>('SemanticDatasetUpdate')
export const chartSchema = contract<ChartDefinition>('ChartDefinition')
export const dashboardSchema = contract<DashboardDefinition>('DashboardDefinition')
export const operationSchema = contract<Operation>('Operation')
export const grantsSchema = contract<GrantsReplace>('GrantsReplace')
export const chartCreateSchema = contract<ChartCreate>('ChartCreate')
export const dashboardCreateSchema = contract<DashboardCreate>('DashboardCreate')
export const deleteSchema = contract<DeleteRequest>('DeleteRequest')
export const snapshotSchema = contract<DocumentSnapshot>('DocumentSnapshot')
export const documentSummarySchema = contract<DocumentSummary>('DocumentSummary')
export const eventPageSchema = contract<EventPage>('EventPage')
export const grantStateSchema = contract<GrantState>('GrantState')
export const receiptSchema = contract<OperationReceipt>('OperationReceipt')
export const publicationSchema = contract<PublicationRequest>('PublicationRequest')
export const restoreSchema = contract<RestoreRequest>('RestoreRequest')
export const revisionSummarySchema = contract<RevisionSummary>('RevisionSummary')
export const permissionSchema = z.fromJSONSchema(
  schema.$defs.GrantSummary.properties.permission as z.core.JSONSchema.JSONSchema,
) as z.ZodType<GrantSummary['permission']>
