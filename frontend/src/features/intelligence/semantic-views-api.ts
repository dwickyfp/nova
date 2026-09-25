import { api } from '@/lib/api-client'

export type SemanticExpression = string | {
  dialects?: { dialect: string; expression: string }[]
}

export type SemanticDefinition = {
  version?: string
  name?: string
  description?: string
  datasets?: {
    name: string
    source?: string
    description?: string
    primary_key?: string[]
    unique_keys?: (string[] | string)[]
    grain?: { keys?: string[] }
    synonyms?: string[]
    ai_context?: unknown
    fields?: {
      name: string
      expression?: SemanticExpression
      datatype?: string
      description?: string
      kind?: string
      dimension?: { is_time?: boolean } | null
      synonyms?: string[]
      sample_values?: unknown[]
      search_strategy?: string
      ai_context?: unknown
    }[]
  }[]
  metrics?: {
    name: string
    expression?: SemanticExpression
    datatype?: string
    description?: string
    base_dataset?: string
    grain?: Record<string, unknown>
    additivity?: string
    dependencies?: string[]
    default_time_dimension?: string
    allowed_dimensions?: string[]
    non_additive_dimensions?: string[]
    synonyms?: string[]
    owner_domain?: string
    supporting_domains?: string[]
    authority?: string
    format?: string
    currency?: string
    unit?: string
    filters?: unknown[]
    visibility?: string
    preferred_relationship_path?: string[]
    ai_context?: unknown
  }[]
  relationships?: {
    name: string
    from?: string
    to?: string
    from_columns?: string[]
    to_columns?: string[]
    cardinality?: string
    preferred?: boolean
    ai_context?: unknown
  }[]
  named_filters?: {
    name: string
    expression?: SemanticExpression
    description?: string
    dataset?: string
    synonyms?: string[]
    ai_context?: unknown
  }[]
  verified_queries?: VerifiedQuery[]
  hierarchies?: Record<string, string[]>
  entities?: Record<string, unknown>
  ai_context?: unknown
  question_routing_instructions?: string
  query_generation_instructions?: string
  [key: string]: unknown
}

export type VerifiedQuery = {
  verified_query_id?: string
  question: string
  semantic_plan: Record<string, unknown>
  verified_sql: string
  expected_result_signature?: string | null
  tags?: string[]
  verified_by?: string
  [key: string]: unknown
}

export type SemanticView = {
  id: string
  name: string
  catalog_name: string
  database_name: string
  schema_name: string
  owner_name: string
  visibility?: 'PUBLIC' | 'PRIVATE'
  active_version: number | null
  status: string
  created_at: string
  updated_at: string
}

export type SemanticValidation = {
  valid: boolean
  errors: string[]
  warnings: string[]
  migration?: {
    source?: string
    legacy_version?: string
    raw_definition_preserved?: boolean
    raw_definition?: Record<string, unknown> | null
  } | null
  regression?: {
    changed: number
    cases: {
      verified_query_id: string
      question: string
      status: string
    }[]
  } | null
  fingerprint?: string
  baseline_version?: number | null
}

export type SemanticVersion = {
  view_id: string
  version: number
  status: string
  definition: SemanticDefinition
  fingerprint: string
  validation: SemanticValidation | null
  created_at: string | null
  validated_at: string | null
  activated_at: string | null
}

export type SemanticViewDetail = SemanticView & { versions: SemanticVersion[] }

export type SemanticPreview = {
  view_id: string
  version: number
  model_fingerprint: string
  semantic_plan: Record<string, unknown>
  generated_sql: string
  confidence: Record<string, unknown>
  relationship_path: string[]
  warnings: string[]
}

export type SemanticQuality = {
  view_id: string
  version: number
  model_fingerprint: string
  valid: boolean
  errors: string[]
  findings: {
    code: string
    severity: string
    message: string
    object_name: string | null
  }[]
  quality: Record<string, unknown>
  verified_queries?: VerifiedQuery[]
  quality_lab?: {
    total: number
    matched: number
    changed: number
    unevaluated?: number
    cases: { verified_query_id: string; question: string; status: string }[]
  } | null
}

const viewPath = (id: string) => `/semantic-views/${encodeURIComponent(id)}`

export const semanticViewsApi = {
  list: () => api.get<SemanticView[]>('/semantic-views'),
  get: (id: string) => api.get<SemanticViewDetail>(viewPath(id)),
  create: (body: { name: string; database: string; definition: string }) =>
    api.post<SemanticViewDetail>('/semantic-views', body),
  addVersion: (id: string, definition: string) =>
    api.post<SemanticVersion>(`${viewPath(id)}/versions`, { definition }),
  validate: (id: string, version: number) =>
    api.post<SemanticValidation>(`${viewPath(id)}/versions/${version}/validate`, {}),
  publish: (id: string, version: number, acknowledgeRegressions: boolean) =>
    api.post<SemanticViewDetail>(`${viewPath(id)}/versions/${version}/publish`, {
      acknowledge_regressions: acknowledgeRegressions,
    }),
  preview: (id: string, version: number, question: string) =>
    api.post<SemanticPreview>(`${viewPath(id)}/versions/${version}/preview`, { question }),
  saveVerifiedQuery: (id: string, baseVersion: number, body: {
    question: string
    semantic_plan: Record<string, unknown>
    verified_sql: string
  }) => api.post<SemanticVersion>(
    `${viewPath(id)}/versions/${baseVersion}/verified-queries`, body,
  ),
  quality: (id: string, version: number) =>
    api.get<SemanticQuality>(`${viewPath(id)}/versions/${version}/quality`),
  query: (id: string, body: {
    metrics: string[]
    dimensions: string[]
    named_filters: string[]
    version: number | null
  }) => api.post<Record<string, unknown>>(`${viewPath(id)}/query`, body),
  deprecate: (id: string) => api.post(`${viewPath(id)}/deprecate`, {}),
  remove: (id: string) => api.delete(viewPath(id)),
}
