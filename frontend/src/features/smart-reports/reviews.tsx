import { useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { Button } from '@/components/ui/button'
import { operationId, reportsPath } from './api'
import { reportSpecSchema, type ReportSpec, type ViewState } from './contracts'

type Review = { review_id: string; title: string; editor: string; draft_revision: number; submitted_at: string }

export function ReportReviews({ state, canPublish, onReview }: {
  state: ViewState
  canPublish: boolean
  onReview: (review: Review, spec: ReportSpec) => Promise<void>
}) {
  const epoch = useAuthStore(s => s.securityEpoch)
  const [operation] = useState(operationId)
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const reviews = useInfiniteQuery({
    queryKey: ['report-reviews', epoch, state.report_id],
    initialPageParam: '',
    queryFn: ({ pageParam, signal }) => api.get<{ reviews: Review[]; next_cursor: string | null }>(`${reportsPath}/${state.report_id}/reviews?after=${encodeURIComponent(pageParam)}`, signal),
    getNextPageParam: page => page.next_cursor ?? undefined,
    enabled: canPublish,
  })
  const submit = async () => {
    setBusy(true)
    try {
      await api.post(`${reportsPath}/${state.report_id}/drafts/${state.draft_id}/reviews`, { operation_id: operation, expected_revision: state.spec_revision })
      setSubmitted(true)
      if (canPublish) await reviews.refetch()
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const review = async (item: Review) => {
    setBusy(true)
    try {
      const result = await api.get<Review & { spec: unknown }>(`${reportsPath}/${state.report_id}/reviews/${item.review_id}`)
      await onReview(result, reportSpecSchema.parse(result.spec))
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  return <section aria-label="Draft reviews" className="space-y-3 border-t pt-4">
    <h3 className="text-sm font-medium">Draft reviews</h3>
    <p className="text-xs text-muted-foreground">Submit this revision for an owner or publisher to review. Later edits stay private until you submit them.</p>
    <Button size="sm" variant="outline" disabled={busy || submitted} onClick={() => void submit()}>{submitted ? 'Revision submitted' : 'Submit for review'}</Button>
    {submitted ? <p role="status" className="text-xs text-muted-foreground">Revision {state.spec_revision} is available to publishers in Draft reviews.</p> : null}
    {canPublish ? reviews.isPending ? <p role="status" className="text-sm">Loading submitted drafts…</p> : reviews.isError ? <p role="alert" className="text-sm">{reviews.error.message}</p> : <>
      {reviews.data?.pages.every(page => page.reviews.length === 0) ? <p className="text-xs text-muted-foreground">No drafts submitted yet.</p> : <ul className="space-y-3">{reviews.data?.pages.flatMap(page => page.reviews).map(item => <li key={item.review_id} className="space-y-1 text-sm">
        <p className="break-words">{item.title}</p>
        <p className="text-xs text-muted-foreground">{item.editor} · revision {item.draft_revision} · {new Date(item.submitted_at).toLocaleString()}</p>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void review(item)}>Review submitted draft</Button>
      </li>)}</ul>}
      {reviews.hasNextPage ? <Button size="sm" variant="ghost" disabled={reviews.isFetchingNextPage} onClick={() => void reviews.fetchNextPage()}>Load more reviews</Button> : null}
    </> : null}
  </section>
}
