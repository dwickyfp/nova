import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth-store'
import { api } from '@/lib/api-client'
import { reportApi, reportsPath, operationId } from './api'
import type { Interaction, Origin, ReportBlock } from './contracts'

export function useReport(block: ReportBlock, origin?: Origin, handoff?: string) {
  const securityEpoch = useAuthStore(s => s.securityEpoch)
  const client = useQueryClient()
  const key = ['smart-report', securityEpoch, block.report_id, block.draft_id, block.draft_revision, origin?.thread_id, handoff]
  const session = useQuery({
    queryKey: [...key, 'session'], retry: false,
    queryFn: async ({ signal }) => {
      const cached = client.getQueryData<Awaited<ReturnType<typeof reportApi.session>>>([...key, 'session'])
      if (cached) return reportApi.session(block.report_id, cached.session_id, signal)
      const saved = !origin && window.history.state?.novaReportSession
      if (!handoff && saved?.report === block.report_id && saved.epoch === securityEpoch && saved.draft === block.draft_id && saved.revision === block.draft_revision) return reportApi.session(block.report_id, saved.id, signal)
      const state = handoff ? await reportApi.consume(handoff) : await reportApi.createSession(block.report_id, {
        draft_id: block.draft_id, revision: block.draft_id ? block.draft_revision : block.published_revision, origin,
      })
      if (state.report_id !== block.report_id) throw new Error('This handoff belongs to another report')
      if (!origin) {
        const url = new URL(window.location.href)
        url.searchParams.delete('handoff')
        window.history.replaceState({ ...window.history.state, novaReportSession: { report: block.report_id, id: state.session_id, epoch: securityEpoch, draft: block.draft_id, revision: block.draft_revision } }, '', url)
      }
      return state
    },
    refetchInterval: 3000,
    refetchIntervalInBackground: false,
  })
  const state = session.data
  const definition = useQuery({
    queryKey: [...key, 'spec', state?.draft_id, state?.spec_revision],
    queryFn: ({ signal }) => reportApi.spec(block.report_id, state!.draft_id, state!.spec_revision, signal),
    enabled: !!state, retry: false,
  })
  const result = useQuery({
    queryKey: [...key, 'render', state?.session_id, state?.state_revision],
    queryFn: ({ signal }) => reportApi.render(state!, signal),
    enabled: !!state && !!definition.data, retry: false, gcTime: 0,
  })
  const event = useMutation({
    mutationFn: (interaction: Interaction) => {
      if (!state) throw new Error('Report is still loading')
      return reportApi.event(state, interaction)
    },
    onMutate: async () => { await client.cancelQueries({ queryKey: [...key, 'render'] }) },
    onSuccess: value => client.setQueryData([...key, 'session'], value.state),
    onError: () => { void session.refetch() },
  })
  return { state, spec: definition.data?.spec, canUndo: definition.data?.canUndo ?? false, canRedo: definition.data?.canRedo ?? false, result: result.data, loading: session.isPending || definition.isPending, querying: result.isFetching, busy: event.isPending,
    error: session.error ?? definition.error ?? event.error ?? result.error, event: event.mutateAsync,
    applySaved: async (preset: string) => {
      if (!state) return
      await client.cancelQueries({ queryKey: [...key, 'render'] })
      const next = await api.post<typeof state>(`${reportsPath}/${state.report_id}/view-sessions/${state.session_id}/saved-view`, { preset_id: preset, operation_id: operationId(), expected_state_revision: state.state_revision })
      client.setQueryData([...key, 'session'], next)
    },
    refresh: () => result.refetch(), reset: () => client.resetQueries({ queryKey: key }),
  }
}
