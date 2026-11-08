import { api } from '@/lib/api-client'

export type Scope = 'current' | 'working'

export type ConceptKind =
  | 'TERM'
  | 'BUSINESS_ENTITY'
  | 'METRIC'
  | 'DIMENSION'
  | 'MEASURE'
  | 'POLICY'
  | 'GOAL'
  | 'BUSINESS_EVENT_TYPE'
  | 'ACTION_TYPE'
  | 'PROCESS'

export type Classification = 'INTERNAL' | 'CONFIDENTIAL' | 'RESTRICTED' | 'HIGHLY_SENSITIVE'

export type RelationType =
  | 'IS_A'
  | 'HAS_PART'
  | 'DERIVES'
  | 'MEASURES'
  | 'APPLIES_TO'
  | 'EQUIVALENT_TO'
  | 'RELATED_TO'
  | 'AFFECTS_GOAL'

export type Domain = {
  id: string
  revision: number
  name: string
  qualified_path: string
  parent_id: string | null
  description: string
  steward_roles: string[]
  visibility_roles: string[]
  status: 'ACTIVE' | 'RETIRED'
  can_review: boolean
  updated_at: string
}

export type ConceptBody = {
  domain_id: string
  kind: ConceptKind
  slug: string
  label: string
  definition: string
  aliases: string[]
  unit: string | null
  grain: string | null
  time_semantics: string | null
  exclusions: string[]
  classification: Classification
}

export type Concept = ConceptBody & {
  id: string
  revision: number
  qualified_name: string
  status: 'APPROVED' | 'DEPRECATED'
  proposal_id: string
  updated_at: string
}

export type ScopeInfo = {
  scope: Scope
  release_id: string | null
  manifest_hash: string | null
}

export type ConceptList = ScopeInfo & { concepts: Concept[]; truncated: boolean }

export type ConceptHistory = {
  revision: number
  status: Concept['status']
  label: string
  proposal_id: string
  changed_by: string
  updated_at: string
}

export type ConceptDetail = ScopeInfo & { concept: Concept; history: ConceptHistory[] }

export type RelatedConcepts = ScopeInfo & {
  concepts: Concept[]
  relations: {
    id: string
    source_id: string
    type: RelationType
    target_id: string
    justification: string
    meaning: string
  }[]
}

export type Binding = {
  id: string
  revision: number
  semantic: { view_id: string; version: number; fingerprint: string }
  metric: string
  purpose: string
  unit: string | null
  status: 'VALIDATED' | 'STALE' | 'REVOKED'
  status_reason: string | null
  usable: boolean
  validated_at: string
}

export type ConceptBindings = ScopeInfo & { representations: Binding[] }

export type ConceptChange = {
  target: 'concept'
  op: 'create' | 'update' | 'deprecate'
  concept_id?: string
  base_revision?: number
  body?: ConceptBody | null
}

export type RelationChange = {
  target: 'relation'
  op: 'create' | 'retire'
  source_id: string
  type: RelationType
  target_id: string
  justification?: string
}

export type RepresentationChange = {
  target: 'representation'
  op: 'create' | 'revoke'
  representation_id?: string
  base_revision?: number
  concept_id?: string
  view_id?: string
  metric?: string
  purpose?: string
}

export type Change = ConceptChange | RelationChange | RepresentationChange

export type ProposalStatus = 'PENDING' | 'APPROVED' | 'APPLIED' | 'REJECTED' | 'WITHDRAWN'

export type Review = {
  operation_id: string
  principal: string
  active_role: string
  domain_ids: string[]
  decision: 'APPROVE' | 'REJECT'
  rationale: string
  at: string
}

export type Proposal = {
  id: string
  revision: number
  title: string
  rationale: string
  source: 'USER' | 'BOOTSTRAP' | 'AGENT' | 'AUTOPILOT' | 'NEWS' | 'ENGINE'
  base_release_id: string | null
  changes: Change[]
  domain_ids: string[]
  submitter: string
  operation_id: string
  status: ProposalStatus
  reviews: Review[]
  created_at: string
  updated_at: string
}

export type ReleaseSummary = {
  id: string
  revision: number
  sequence: number
  base_release_id: string | null
  manifest_hash: string
  status: 'PREPARED' | 'BLOCKED' | 'PUBLISHED'
  note: string
  published_at: string | null
  created_at: string
  actor: { principal: string; active_role: string }
  member_counts: Partial<Record<'domain' | 'concept' | 'relation' | 'representation', number>>
  /** Present only for ACCOUNTADMIN. */
  issues?: string[]
  warnings?: string[]
}

export type CurrentRelease = {
  release: ReleaseSummary | null
  previous_release_id: string | null
  reason?: string
  moved_at?: string
}

export type DomainCreate = {
  name: string
  parent_id: string | null
  description: string
  steward_roles: string[]
  visibility_roles: string[]
}

export type DomainUpdate = {
  description: string
  steward_roles: string[]
  visibility_roles: string[]
  status: Domain['status']
  expected_revision: number
}

/** One published object that proves a derived item. */
export type DerivedEvidence = {
  semantic: { view_id: string; version: number; fingerprint: string }
  view_name: string
  method: 'dataset' | 'field' | 'relationship' | 'metric' | 'metric_expression' | 'claim'
  source: string
  confidence: number
}

/** Meaning Nova compiled from published Semantic Views: context, never official. */
export type DerivedItem = {
  id: string
  key: string
  item_type: 'concept' | 'relation'
  concept_kind: ConceptKind | null
  label: string
  definition: string
  aliases: string[]
  unit: string | null
  is_time: boolean
  metric: string | null
  relation_type: RelationType | null
  source_key: string | null
  target_key: string | null
  role: string | null
  roles: string[]
  official_concept: string | null
  confidence: number
  evidence: DerivedEvidence[]
}

export type DerivedCompilation = {
  id: string
  view_id: string
  view_name: string
  version: number
  counts: Record<string, number>
  updated_at: string
}

export type DerivedLayer = {
  items: DerivedItem[]
  compilations: DerivedCompilation[]
  compiler_version: string
}

/** Proposals Nova drafted from the derived layer, and what it left for a person. */
export type DerivedPromotion = {
  proposals: Proposal[]
  skipped: { key: string; label: string; reason: string }[]
}

/** One meaning check result; advice for stewards, never a block. */
export type LintFinding = {
  rule: 'definition_formula' | 'adjustment_ignored' | 'ambiguous_name' | 'unbound_metric'
  severity: 'error' | 'warning'
  concept_id: string
  concept: string
  message: string
  evidence: string[]
}

export type LintReport = {
  findings: LintFinding[]
  counts: { error: number; warning: number }
}

/** Names Nova proposed, and what its validator dropped. */
export type Enrichment = {
  proposals: Proposal[]
  rejected: { concept: string; value: string; reason: string }[]
}

/** A proportion with its Wilson 95% interval; ``value`` is null until measured. */
export type Measured = {
  value: number | null
  successes: number
  n: number
  interval_95: [number, number] | null
}

export type CalibrationBin = { low: number; high: number; n: number; confidence: number; accuracy: number }

export type Kappa = { kappa: number | null; agreement: number | null; n: number }

export type AnswerSummary = {
  run_id: string
  at: string
  release_id?: string | null
  set?: string
  answer_accuracy: Measured
  reliability: { value: number | null; n: number }
  wrong_answers: Measured
  abstention: Measured
  clarification: Measured
  failed: number
  outcomes: Record<string, number>
  latency_ms_p50: number | null
}

export type ClaimSummary = {
  run_id: string
  at: string
  claim_accuracy: { value: number | null; n: number; interval_95: [number, number] | null; unsampled: string[] }
  by_stratum: Record<string, Measured>
  unsure: number
  calibration: { ece: number | null; brier: number | null; n: number; bins: CalibrationBin[] }
  agreement: Record<string, Kappa>
  population?: Record<string, number>
}

export type DriftFinding = {
  column: string
  rule: 'key_no_longer_unique' | 'null_rate_rose' | 'distinct_values_shifted' | 'join_lost_integrity'
  before: number
  after: number
}

export type OsiExport = {
  release_id: string
  manifest_hash: string
  complete: boolean
  documents: { view_id: string; name: string; document: Record<string, unknown> }[]
}

export type LearnReport = {
  proposals: { id: string; title: string; changes: number; question?: string }[]
  skipped: Record<string, string>[]
}

export type JoinScore = {
  precision: Measured
  recall: Measured
  f1: number
  predicted: number
  gold: number
}

/** What the latest join discovery found and how well its own probabilities held up. */
export type JoinSummary = {
  run_id: string
  at: string | null
  databases: string[]
  profiled_columns: number
  profile_seconds: number
  discover_seconds: number
  candidates: number
  joins: JoinScore
  joins_reviewed: JoinScore | null
  undeclared_reviewed: number
  calibration: {
    ece: number
    brier: number
    auc: number | null
    n: number
    positives: number
    bins: CalibrationBin[]
  } | null
  drift: { compared_with: string; findings: DriftFinding[] } | null
  history: { families: number; pairs: number } | null
  decisions: {
    labelled: number
    automatic: Measured
    accepted_precision: Measured
    rejected_misses: Measured
    review_queue: number
  } | null
}

export type OntologyMetrics = {
  measured: {
    answers: AnswerSummary | null
    baseline: AnswerSummary | null
    effect: {
      n: number
      without: Measured
      with: Measured
      difference: number | null
      mcnemar: { gained: number; lost: number; p_value: number }
    } | null
    claims: ClaimSummary | null
    gold: {
      set_name: string
      version: number
      active: Record<string, number>
      rejected: number
      agreement: Record<string, Kappa>
    } | null
    joins: JoinSummary | null
    history: {
      run_id: string
      release_id: string | null
      at: string | null
      set: string
      answer_accuracy: Measured | null
    }[]
  }
  live: {
    metric_coverage: Measured
    question_grounding: Measured | null
    domains: { domain: string; concepts: number; metrics: number; bound: number }[]
    stale_bindings: number
    checks: Record<string, number>
    engine_proposals: Record<string, number>
    engine_precision: Measured
  }
  not_measured: string[]
}

export type EvaluationRun = {
  id: string
  kind: 'gold' | 'answers' | 'claims' | 'joins' | 'judge'
  set_name: string
  version: number
  mode: 'with_ontology' | 'without_ontology' | null
  release_id: string | null
  status: 'RUNNING' | 'COMPLETED' | 'FAILED'
  stage: string
  done: number
  total: number
  summary: Record<string, unknown>
  error: string | null
  created_at: string
  updated_at: string
  actor: { principal: string }
}

export type EvaluationResult = {
  id: string
  run_id: string
  item: string
  outcome: string
  detail: Record<string, unknown>
  reviews: { reviewer: string; kind: string; verdict: string; reason: string }[]
}

export type EvaluationCase = {
  id: string
  kind: 'answerable' | 'unanswerable' | 'ambiguous'
  intent: string
  language: 'id' | 'en'
  question: string
  view_name: string
  meaning: string
  expected: 'answer' | 'abstain' | 'clarify'
  status: 'DRAFT' | 'ACTIVE' | 'REJECTED'
  reviews: { reviewer: string; kind: string; verdict: string; reason: string }[]
}

const id = encodeURIComponent

/** A fresh idempotency key; a retried request reuses the one it was sent with. */
export function operationId(): string {
  return crypto.randomUUID()
}

export const ontologyApi = {
  domains: () => api.get<Domain[]>('/ontology/domains'),
  createDomain: (body: DomainCreate) => api.post<Domain>('/ontology/domains', body),
  updateDomain: (domainId: string, body: DomainUpdate) =>
    api.put<Domain>(`/ontology/domains/${id(domainId)}`, body),

  concepts: (params: { scope: Scope; domainId?: string; q?: string; includeDeprecated?: boolean }) => {
    const query = new URLSearchParams({ scope: params.scope })
    if (params.domainId) query.set('domain_id', params.domainId)
    if (params.q) query.set('q', params.q)
    if (params.includeDeprecated) query.set('include_deprecated', 'true')
    return api.get<ConceptList>(`/ontology/concepts?${query}`)
  },
  concept: (conceptId: string, scope: Scope) =>
    api.get<ConceptDetail>(`/ontology/concepts/${id(conceptId)}?scope=${scope}`),
  related: (conceptId: string, scope: Scope) =>
    api.get<RelatedConcepts>(`/ontology/concepts/${id(conceptId)}/related?scope=${scope}`),
  bindings: (conceptId: string, scope: Scope) =>
    api.get<ConceptBindings>(
      `/ontology/concepts/${id(conceptId)}/representations?scope=${scope}`
    ),

  proposals: (status?: ProposalStatus) =>
    api.get<Proposal[]>(status ? `/ontology/proposals?status=${status}` : '/ontology/proposals'),
  propose: (body: {
    title: string
    rationale: string
    changes: Change[]
    operation_id: string
  }) => api.post<Proposal>('/ontology/proposals', body),
  bootstrap: (body: { view_id: string; domain_id: string; operation_id: string }) =>
    api.post<Proposal>('/ontology/bootstrap', body),
  review: (
    proposalId: string,
    body: { decision: Review['decision']; rationale: string; operation_id: string }
  ) => api.post<Proposal>(`/ontology/proposals/${id(proposalId)}/review`, body),
  apply: (proposalId: string) =>
    api.post<Proposal>(`/ontology/proposals/${id(proposalId)}/apply`),
  withdraw: (proposalId: string) =>
    api.post<Proposal>(`/ontology/proposals/${id(proposalId)}/withdraw`),

  derived: () => api.get<DerivedLayer>('/ontology/derived'),
  compileDerived: (viewId?: string) =>
    api.post<{ compilations: DerivedCompilation[] }>(
      '/ontology/derived/compile',
      viewId ? { view_id: viewId } : {}
    ),
  lint: () => api.get<LintReport>('/ontology/lint'),
  metrics: () => api.get<OntologyMetrics>('/ontology/metrics'),
  evaluationRuns: () => api.get<EvaluationRun[]>('/ontology/evaluation/runs'),
  evaluationResults: (runId: string) =>
    api.get<EvaluationResult[]>(`/ontology/evaluation/runs/${id(runId)}/results`),
  evaluationCases: (setName: string) =>
    api.get<EvaluationCase[]>(`/ontology/evaluation/cases?set_name=${id(setName)}`),
  startAnswers: (body: { set_name: string; mode: 'with_ontology' | 'without_ontology' }) =>
    api.post<EvaluationRun>('/ontology/evaluation/answers', body),
  startClaims: () => api.post<EvaluationRun>('/ontology/evaluation/claims', {}),
  startJoins: () => api.post<EvaluationRun>('/ontology/evaluation/joins', {}),
  startJudge: (setName: string) =>
    api.post<EvaluationRun>('/ontology/evaluation/judge', { set_name: setName }),
  learn: () => api.post<LearnReport>('/ontology/learn', { operation_id: operationId() }),
  exportOsi: () => api.get<OsiExport>('/ontology/osi/export'),
  startGold: (body: { set_name: string; view_ids: string[]; per_metric?: number }) =>
    api.post<EvaluationRun>('/ontology/evaluation/gold', body),
  stepRun: (runId: string) =>
    api.post<EvaluationRun>(`/ontology/evaluation/runs/${id(runId)}/step`),
  enrich: (body: { operation_id: string; domain_id?: string | null }) =>
    api.post<Enrichment>('/ontology/enrich', body),
  proposeDerived: (body: { operation_id: string; shared_domain_id?: string | null }) =>
    api.post<DerivedPromotion>('/ontology/derived/propose', body),

  releases: () => api.get<ReleaseSummary[]>('/ontology/releases'),
  currentRelease: () => api.get<CurrentRelease>('/ontology/releases/current'),
  prepareRelease: (note: string) => api.post<ReleaseSummary>('/ontology/releases', { note }),
  publishRelease: (releaseId: string) =>
    api.post<ReleaseSummary>(`/ontology/releases/${id(releaseId)}/publish`),
  rollback: (releaseId: string, reason: string) =>
    api.post<{ release_id: string; previous_release_id: string | null }>(
      `/ontology/releases/${id(releaseId)}/rollback`,
      { reason }
    ),
}
