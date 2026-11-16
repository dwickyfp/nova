import { useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, RotateCcw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Textarea } from '@/components/ui/textarea'
import { api, ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import type { DashboardDefinition } from '../contracts'
import { commentsKey, MAX_COMMENT, useComments, type DashboardComment } from './comments'

function when(iso: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso))
}

/**
 * Notes about the dashboard, a page or a widget. Text is shown exactly as
 * written; comments never contain or change data.
 */
export function CommentsPanel({ open, onOpenChange, dashboardId, definition, pageId, selectedWidgetId, canComment, canManage }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  dashboardId: string
  definition: DashboardDefinition
  pageId: string
  selectedWidgetId: string | null
  canComment: boolean
  canManage: boolean
}) {
  const epoch = useAuthStore(state => state.securityEpoch)
  const client = useQueryClient()
  const comments = useComments(dashboardId, open)
  const [body, setBody] = useState('')
  const [about, setAbout] = useState<'dashboard' | 'page' | 'widget'>(selectedWidgetId ? 'widget' : 'dashboard')
  const [showResolved, setShowResolved] = useState(false)
  const path = `/viz/dashboards/${encodeURIComponent(dashboardId)}/comments`
  const refresh = () => client.invalidateQueries({ queryKey: commentsKey(epoch, dashboardId) })

  const pages = useMemo(() => new Map(definition.pages.map(page => [page.page_id, page])), [definition])
  const widgetTitle = (comment: Pick<DashboardComment, 'page_id' | 'widget_id'>) => {
    const widget = pages.get(comment.page_id ?? '')?.widgets?.find(item => item.widget_id === comment.widget_id)
    return widget ? widget.title || (widget.kind === 'text' ? 'Text' : 'Chart') : 'A removed widget'
  }
  const place = (comment: DashboardComment) => !comment.page_id ? 'Dashboard'
    : comment.widget_id ? `${pages.get(comment.page_id)?.title ?? 'A removed page'} · ${widgetTitle(comment)}`
      : pages.get(comment.page_id)?.title ?? 'A removed page'

  const post = useMutation({
    mutationFn: () => api.request<DashboardComment>(path, {
      method: 'POST', securityEpoch: epoch, body: JSON.stringify({
        comment_id: crypto.randomUUID(), body: body.trim(),
        ...(about !== 'dashboard' ? { page_id: pageId } : {}),
        ...(about === 'widget' && selectedWidgetId ? { widget_id: selectedWidgetId } : {}),
      }),
    }),
    onSuccess: () => { setBody(''); void refresh() },
    onError: error => toast.error(error instanceof ApiError && [403, 404].includes(error.status)
      ? 'Your access to this dashboard does not include commenting.' : 'The comment could not be posted.'),
  })
  const resolve = useMutation({
    mutationFn: (comment: DashboardComment) => api.request(`${path}/${comment.comment_id}`, {
      method: 'PATCH', securityEpoch: epoch, body: JSON.stringify({ resolved: !comment.resolved_at }) }),
    onSuccess: () => void refresh(),
    onError: () => toast.error('The comment could not be changed.'),
  })
  const remove = useMutation({
    mutationFn: (comment: DashboardComment) => api.request(`${path}/${comment.comment_id}`, {
      method: 'DELETE', securityEpoch: epoch, body: '{}' }),
    onSuccess: () => void refresh(),
    onError: () => toast.error('The comment could not be removed.'),
  })

  const all = comments.data ?? []
  const resolvedCount = all.filter(item => item.resolved_at).length
  const shown = all.filter(item => showResolved || !item.resolved_at)

  return <Sheet open={open} onOpenChange={onOpenChange}>
    <SheetContent className='flex w-full flex-col gap-0 p-0 sm:max-w-md'>
      <SheetHeader className='border-b p-4'>
        <SheetTitle>Comments</SheetTitle>
        <SheetDescription>Notes for people working on this dashboard. They never change its data.</SheetDescription>
      </SheetHeader>
      <div className='min-h-0 flex-1 space-y-3 overflow-y-auto p-4'>
        {comments.isPending ? <p className='text-sm text-muted-foreground'>Loading comments…</p>
          : comments.isError ? <p className='text-sm text-muted-foreground'>Comments could not be loaded.</p>
            : !shown.length ? <p className='text-sm text-muted-foreground'>{all.length ? 'Every comment is resolved.' : 'No comments yet.'}</p>
              : <ul className='space-y-3'>{shown.map(comment => <li key={comment.comment_id}
                className='space-y-1.5 rounded-md border p-3' data-resolved={comment.resolved_at ? 'true' : undefined}>
                <div className='flex items-baseline gap-2'>
                  <span className='text-sm font-medium'>{comment.author}</span>
                  <span className='truncate text-xs text-muted-foreground'>{when(comment.created_at)}</span>
                </div>
                <p className='text-xs text-muted-foreground'>{place(comment)}</p>
                <p className='text-sm whitespace-pre-wrap break-words'>{comment.body}</p>
                <div className='flex items-center gap-1'>
                  {comment.resolved_at && <span className='mr-auto text-xs text-muted-foreground'>Resolved by {comment.resolved_by}</span>}
                  {canComment && <Button variant='ghost' size='sm' className='h-7 px-2 text-xs' disabled={resolve.isPending}
                    onClick={() => resolve.mutate(comment)}>
                    {comment.resolved_at ? <><RotateCcw />Reopen</> : <><Check />Resolve</>}
                  </Button>}
                  {(comment.mine || canManage) && <Button variant='ghost' size='icon' className='size-7'
                    aria-label={`Remove the comment by ${comment.author}`} disabled={remove.isPending}
                    onClick={() => remove.mutate(comment)}><Trash2 /></Button>}
                </div>
              </li>)}</ul>}
        {resolvedCount > 0 && <Button variant='link' size='sm' className='h-auto p-0 text-xs' onClick={() => setShowResolved(!showResolved)}>
          {showResolved ? 'Hide resolved' : `Show resolved (${resolvedCount})`}
        </Button>}
      </div>
      {canComment ? <form className='space-y-2 border-t p-4' onSubmit={event => { event.preventDefault(); if (body.trim()) post.mutate() }}>
        <div className='flex items-center gap-2'>
          <Label htmlFor='comment-about' className='shrink-0 text-xs text-muted-foreground'>About</Label>
          <Select value={about} onValueChange={value => setAbout(value as typeof about)}>
            <SelectTrigger id='comment-about' className='h-8 w-full'><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value='dashboard'>The whole dashboard</SelectItem>
              <SelectItem value='page'>This page</SelectItem>
              {selectedWidgetId && <SelectItem value='widget'>The selected widget</SelectItem>}
            </SelectContent>
          </Select>
        </div>
        <Textarea aria-label='Comment' value={body} maxLength={MAX_COMMENT} rows={3} placeholder='Write a comment'
          onChange={event => setBody(event.target.value)} />
        <div className='flex items-center justify-between'>
          <span className='text-xs text-muted-foreground'>{body.length > MAX_COMMENT - 200 ? `${MAX_COMMENT - body.length} characters left` : ''}</span>
          <Button type='submit' size='sm' disabled={!body.trim() || post.isPending}>{post.isPending ? 'Posting…' : 'Post'}</Button>
        </div>
      </form> : <p className='border-t p-4 text-xs text-muted-foreground'>Your access lets you read comments but not write them.</p>}
    </SheetContent>
  </Sheet>
}
