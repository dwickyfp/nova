import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PackageCheck } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { LoadingLines } from '@/components/ui/loading-overlay'
import { StatusBadge } from '@/components/ui/status-badge'
import { Textarea } from '@/components/ui/textarea'
import { ontologyApi, type ReleaseSummary } from './api'
import { RELEASE_TONE, formatDate, useIsAdmin, useOntologyKey } from './model'

const STATUS_LABEL: Record<ReleaseSummary['status'], string> = {
  PREPARED: 'Ready to publish',
  BLOCKED: 'Blocked',
  PUBLISHED: 'Published',
}

function counts(release: ReleaseSummary): string {
  const parts: [keyof ReleaseSummary['member_counts'], string][] = [
    ['concept', 'concept'],
    ['relation', 'link'],
    ['representation', 'metric binding'],
    ['domain', 'domain'],
  ]
  return parts
    .map(([key, noun]) => {
      const value = release.member_counts[key] ?? 0
      return `${value} ${noun}${value === 1 ? '' : 's'}`
    })
    .join(', ')
}

export function ReleasesTab() {
  const isAdmin = useIsAdmin()
  const queryClient = useQueryClient()
  const prefix = useOntologyKey()
  const [note, setNote] = useState('')
  const [rollbackTo, setRollbackTo] = useState<ReleaseSummary | null>(null)
  const current = useQuery({
    queryKey: useOntologyKey('releases', 'current'),
    queryFn: ontologyApi.currentRelease,
  })
  const releases = useQuery({ queryKey: useOntologyKey('releases'), queryFn: ontologyApi.releases })
  const refresh = () => queryClient.invalidateQueries({ queryKey: prefix })
  const onError = (error: Error) => toast.error(error.message)

  const prepare = useMutation({
    mutationFn: () => ontologyApi.prepareRelease(note.trim()),
    onSuccess: (release) => {
      setNote('')
      toast[release.status === 'BLOCKED' ? 'error' : 'success'](
        release.status === 'BLOCKED'
          ? `${release.id} is blocked; fix the issues listed under it`
          : `${release.id} is ready to publish`
      )
      refresh()
    },
    onError,
  })
  const publish = useMutation({
    mutationFn: (releaseId: string) => ontologyApi.publishRelease(releaseId),
    onSuccess: (release) => {
      toast.success(`${release.id} is now used by Studio`)
      refresh()
    },
    onError,
  })

  const currentId = current.data?.release?.id ?? null

  return (
    <div className='flex flex-col gap-4'>
      <section className='rounded-lg border bg-card p-4'>
        <h3 className='text-sm font-medium'>In use by Studio</h3>
        {current.isLoading ? (
          <LoadingLines rows={2} className='mt-2' />
        ) : current.data?.release ? (
          <div className='mt-1 text-sm'>
            <p>
              <span className='font-medium'>{current.data.release.id}</span>, published{' '}
              {formatDate(current.data.release.published_at)}
            </p>
            <p className='text-muted-foreground'>{counts(current.data.release)}</p>
            {current.data.reason && current.data.previous_release_id ? (
              <p className='mt-1 text-xs text-muted-foreground'>
                Replaced {current.data.previous_release_id}: {current.data.reason}
              </p>
            ) : null}
          </div>
        ) : (
          <p className='mt-1 text-sm text-muted-foreground'>
            No release is published, so Studio answers metric questions without business
            definitions.
          </p>
        )}
      </section>

      {isAdmin ? (
        <div className='flex flex-wrap items-end gap-2'>
          <div className='grid min-w-56 flex-1 gap-1.5 sm:max-w-md'>
            <Label htmlFor='release-note'>Release note</Label>
            <Input
              id='release-note'
              value={note}
              placeholder='What this release changes'
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
          <Button disabled={prepare.isPending} onClick={() => prepare.mutate()}>
            {prepare.isPending ? 'Preparing' : 'Prepare a release'}
          </Button>
        </div>
      ) : (
        <p className='text-sm text-muted-foreground'>
          ACCOUNTADMIN prepares and publishes releases after the stewards approve the changes.
        </p>
      )}

      {releases.isLoading ? (
        <LoadingLines rows={4} />
      ) : releases.isError ? (
        <EmptyState
          variant='error'
          icon={PackageCheck}
          title='Releases could not be loaded'
          description={releases.error.message}
          action={<Button variant='outline' onClick={() => releases.refetch()}>Try again</Button>}
        />
      ) : !releases.data?.length ? (
        <EmptyState
          icon={PackageCheck}
          title='No releases yet'
          description='A release freezes the approved definitions so every answer can be traced to them.'
        />
      ) : (
        <ul className='grid gap-3'>
          {releases.data.map((release) => {
            const stale = release.status === 'PREPARED' && release.base_release_id !== currentId
            return (
              <li key={release.id} className='rounded-lg border bg-card p-4'>
                <div className='flex flex-wrap items-start justify-between gap-2'>
                  <div className='min-w-0'>
                    <p className='font-medium'>
                      {release.id}
                      {release.id === currentId ? (
                        <span className='ml-2 text-xs font-normal text-muted-foreground'>in use</span>
                      ) : null}
                    </p>
                    <p className='text-xs text-muted-foreground'>
                      Prepared by {release.actor.principal}, {formatDate(release.created_at)}
                    </p>
                  </div>
                  <StatusBadge tone={RELEASE_TONE[release.status]}>
                    {STATUS_LABEL[release.status]}
                  </StatusBadge>
                </div>
                {release.note ? <p className='mt-2 text-sm'>{release.note}</p> : null}
                <p className='mt-2 text-sm text-muted-foreground'>{counts(release)}</p>
                {release.issues?.length ? (
                  <ul className='mt-2 grid gap-1 text-sm text-destructive'>
                    {release.issues.map((issue) => (
                      <li key={issue}>{issue}</li>
                    ))}
                  </ul>
                ) : null}
                {release.warnings?.length ? (
                  <ul className='mt-2 grid gap-1 text-sm text-warning-strong'>
                    {release.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                ) : null}
                {isAdmin ? (
                  <div className='mt-3 flex flex-wrap items-center gap-2'>
                    {release.status === 'PREPARED' ? (
                      <>
                        <Button
                          size='sm'
                          disabled={stale || publish.isPending}
                          onClick={() => publish.mutate(release.id)}
                        >
                          Publish
                        </Button>
                        {stale ? (
                          <span className='text-xs text-muted-foreground'>
                            Another release was published after this one was prepared. Prepare
                            again.
                          </span>
                        ) : null}
                      </>
                    ) : null}
                    {release.status === 'PUBLISHED' && release.id !== currentId ? (
                      <Button size='sm' variant='outline' onClick={() => setRollbackTo(release)}>
                        Use this release again
                      </Button>
                    ) : null}
                  </div>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}

      <RollbackDialog release={rollbackTo} onClose={() => setRollbackTo(null)} onDone={refresh} />
    </div>
  )
}

function RollbackDialog({
  release,
  onClose,
  onDone,
}: {
  release: ReleaseSummary | null
  onClose: () => void
  onDone: () => void
}) {
  const [reason, setReason] = useState('')
  const rollback = useMutation({
    mutationFn: () => ontologyApi.rollback(release!.id, reason.trim()),
    onSuccess: () => {
      toast.success(`Studio uses ${release!.id} again`)
      setReason('')
      onDone()
      onClose()
    },
    onError: (error: Error) => toast.error(error.message),
  })
  return (
    <Dialog open={release !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className='sm:max-w-lg'>
        <DialogHeader>
          <DialogTitle>Use {release?.id} again</DialogTitle>
          <DialogDescription>
            Studio answers new questions with this release. Earlier answers keep the release they
            used, and no approved version is deleted.
          </DialogDescription>
        </DialogHeader>
        <div className='grid gap-1.5'>
          <Label htmlFor='rollback-reason'>Reason</Label>
          <Textarea
            id='rollback-reason'
            rows={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!reason.trim() || rollback.isPending} onClick={() => rollback.mutate()}>
            {rollback.isPending ? 'Switching' : 'Use this release'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
