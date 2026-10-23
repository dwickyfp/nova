import { api } from '@/lib/api-client'
import { reportSpecSchema, viewStateSchema, type Origin, type ReportSpec, type ViewState, type ReportSummary, type RenderResult, type Interaction, type PatchRequest } from './contracts'

export const reportsPath = '/agents/studio/reports'
export const operationId = () => crypto.randomUUID()
export const reportApi = {
  list: (after = '', signal?: AbortSignal) => api.get<{ reports: ReportSummary[]; next_cursor: string | null }>(`${reportsPath}?after=${encodeURIComponent(after)}`, signal),
  get: (id: string, signal?: AbortSignal) => api.get<ReportSummary>(`${reportsPath}/${id}`, signal),
  plan: (prompt: string) => api.post<{ spec?: ReportSpec; clarification?: string }>(`${reportsPath}/plan`, { prompt }),
  create: (spec: ReportSpec) => api.post<{ report_id: string; draft_id: string; draft_revision: number }>(reportsPath, { operation_id: operationId(), spec: reportSpecSchema.parse(spec) }),
  spec: async (id: string, draft: string | null, revision: number, signal?: AbortSignal) => {
    const result = await api.get<{ spec: unknown; revision: number; can_undo?: boolean; can_redo?: boolean }>(`${reportsPath}/${id}/${draft ? `drafts/${draft}?revision=${revision}` : `revisions/${revision}`}`, signal)
    return { spec: reportSpecSchema.parse(result.spec), revision: result.revision, canUndo: result.can_undo ?? false, canRedo: result.can_redo ?? false }
  },
  session: async (id: string, sessionId: string, signal?: AbortSignal) => viewStateSchema.parse(await api.get(`${reportsPath}/${id}/view-sessions/${sessionId}`, signal)),
  createSession: async (id: string, body: { draft_id?: string | null; revision?: number | null; origin?: Origin; preset_id?: string }) => viewStateSchema.parse(await api.post(`${reportsPath}/${id}/view-sessions`, body)),
  consume: async (handoff: string) => viewStateSchema.parse(await api.post(`${reportsPath}/handoffs/consume`, { handoff_id: handoff })),
  event: (state: ViewState, event: Interaction) => api.post<{ state: ViewState }>(`${reportsPath}/${state.report_id}/view-sessions/${state.session_id}/events`, { ...event, event_id: operationId(), spec_revision: state.spec_revision, expected_state_revision: state.state_revision }),
  render: (state: ViewState, signal?: AbortSignal) => api.post<RenderResult>(`${reportsPath}/${state.report_id}/render`, { view_session_id: state.session_id, expected_state_revision: state.state_revision }, signal),
  handoff: (state: ViewState) => api.post<{ target_path: string }>(`${reportsPath}/${state.report_id}/handoffs`, { view_session_id: state.session_id, expected_state_revision: state.state_revision }),
  draft: (id: string) => api.post<{ draft_id: string; revision: number }>(`${reportsPath}/${id}/drafts`, { operation_id: operationId() }),
  patch: (id: string, draft: string, body: PatchRequest) => api.patch<{ revision: number }>(`${reportsPath}/${id}/drafts/${draft}`, body),
  preview: (id: string, draft: string, body: PatchRequest) => api.post<{ before: ReportSpec; after: ReportSpec }>(`${reportsPath}/${id}/drafts/${draft}/validate-patch`, body),
  proposal: (id: string, draft: string, prompt: string) => api.post<{ patch?: PatchRequest; clarification?: string }>(`${reportsPath}/${id}/drafts/${draft}/proposals`, { prompt }),
  publish: (id: string, draft: string, revision: number, published: number) => api.post(`${reportsPath}/${id}/drafts/${draft}/publish`, { operation_id: operationId(), expected_revision: revision, expected_published_revision: published }),
}
