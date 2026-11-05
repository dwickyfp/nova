import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Loader2, Pencil, Plus, X } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { LoadingLines } from '@/components/ui/loading-overlay'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import {
  CLASSIFICATIONS,
  annotateSecurable,
  deleteTag,
  getLineage,
  parseTagInput,
  putTags,
  resolveSecurable,
  type CatalogTag,
  type SecurableDetail,
  type SecurableSummary,
} from './catalog-metadata-api'

const NO_CLASSIFICATION = 'none'

function message(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

// ── Tags ─────────────────────────────────────────────────────

export function TagList({
  tags,
  onRemove,
  busy,
}: {
  tags: CatalogTag[]
  onRemove?: (key: string) => void
  busy?: boolean
}) {
  if (tags.length === 0) return <span className='text-sm text-muted-foreground'>No tags</span>
  return (
    <ul className='flex flex-wrap gap-1.5' aria-label='Tags'>
      {tags.map((tag) => (
        <li key={tag.key}>
          <Badge variant='outline' className='gap-1 font-normal'>
            <span className='font-medium'>{tag.key}</span>
            {tag.value && <span className='text-muted-foreground'>= {tag.value}</span>}
            {onRemove && (
              <button
                type='button'
                className='-me-1 rounded-sm p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50'
                aria-label={`Remove tag ${tag.key}`}
                disabled={busy}
                onClick={() => onRemove(tag.key)}
              >
                <X className='h-3 w-3' />
              </button>
            )}
          </Badge>
        </li>
      ))}
    </ul>
  )
}

function TagInput({ onAdd, busy, id }: { onAdd: (text: string) => void; busy?: boolean; id: string }) {
  const [text, setText] = useState('')
  const parsed = text.trim() ? parseTagInput(text) : null
  const invalid = text.trim() !== '' && parsed === null
  const submit = () => {
    if (!parsed) return
    onAdd(text)
    setText('')
  }
  return (
    <div className='space-y-1'>
      <div className='flex items-center gap-2'>
        <Input
          id={id}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              submit()
            }
          }}
          placeholder='key or key=value'
          aria-invalid={invalid}
          aria-describedby={invalid ? `${id}-error` : undefined}
          className='h-8 max-w-56 text-sm'
          disabled={busy}
        />
        <Button type='button' variant='outline' size='sm' disabled={!parsed || busy} onClick={submit}>
          <Plus className='me-1 h-3.5 w-3.5' />
          Add tag
        </Button>
      </div>
      {invalid && (
        <p id={`${id}-error`} className='text-xs text-destructive'>
          A tag key starts with a letter and uses letters, digits, and _ . : -
        </p>
      )}
    </div>
  )
}

// ── Column editor ────────────────────────────────────────────

function ColumnMetadataDialog({
  column,
  onOpenChange,
  onSaved,
}: {
  column: SecurableSummary | null
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const [comment, setComment] = useState(column?.comment ?? '')
  const [classification, setClassification] = useState(column?.classification ?? NO_CLASSIFICATION)
  const [tags, setTags] = useState<CatalogTag[]>(column?.tags ?? [])
  const [saving, setSaving] = useState(false)

  if (!column) return null

  const save = async () => {
    setSaving(true)
    try {
      const nextClassification = classification === NO_CLASSIFICATION ? null : classification
      if ((column.comment ?? '') !== comment.trim() ||
          (column.classification ?? null) !== nextClassification) {
        await annotateSecurable(column.id, {
          comment: comment.trim() || null,
          classification: nextClassification,
        })
      }
      const before = new Map(column.tags.map((tag) => [tag.key, tag.value]))
      const after = new Map(tags.map((tag) => [tag.key, tag.value]))
      for (const key of before.keys()) if (!after.has(key)) await deleteTag(column.id, key)
      const changed = tags.filter((tag) => !before.has(tag.key) || before.get(tag.key) !== tag.value)
      if (changed.length) await putTags(column.id, changed.map(({ key, value }) => ({ key, value })))
      toast.success(`Saved metadata for ${column.column_name}`)
      onSaved()
      onOpenChange(false)
    } catch (error) {
      toast.error(message(error, 'Could not save column metadata'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-lg'>
        <DialogHeader>
          <DialogTitle>Column {column.column_name}</DialogTitle>
          <DialogDescription>
            Business metadata for this column. Ranger policies still decide who can read its values.
          </DialogDescription>
        </DialogHeader>
        <div className='space-y-4 py-1'>
          <div className='space-y-2'>
            <Label htmlFor='column-classification'>Classification</Label>
            <Select value={classification} onValueChange={setClassification}>
              <SelectTrigger id='column-classification' className='w-56'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_CLASSIFICATION}>Not classified</SelectItem>
                {CLASSIFICATIONS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {value.charAt(0) + value.slice(1).toLowerCase()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className='space-y-2'>
            <Label htmlFor='column-comment'>Description</Label>
            <Textarea
              id='column-comment'
              value={comment}
              maxLength={4096}
              onChange={(event) => setComment(event.target.value)}
              placeholder='What this column holds, in business terms'
            />
          </div>
          <div className='space-y-2'>
            <Label htmlFor='column-tag'>Tags</Label>
            <TagList
              tags={tags}
              onRemove={(key) => setTags((current) => current.filter((tag) => tag.key !== key))}
            />
            <TagInput
              id='column-tag'
              onAdd={(text) => {
                const tag = parseTagInput(text)
                if (!tag) return
                setTags((current) => [
                  ...current.filter((item) => item.key !== tag.key),
                  { key: tag.key, value: tag.value },
                ])
              }}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving && <Loader2 className='me-2 h-4 w-4 animate-spin' />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Relation section ─────────────────────────────────────────

/**
 * Tags, description, owner, column classifications, lineage, and dependents of
 * one relation, read from the Nova Catalog as the signed-in role sees them.
 */
export function CatalogMetadataSection({
  catalog,
  database,
  object,
}: {
  catalog: string
  database: string
  object: string
}) {
  const queryClient = useQueryClient()
  const securityEpoch = useAuthStore((state) => state.securityEpoch)
  const key = ['catalog-securable', securityEpoch, catalog, database, object]
  const detail = useQuery<SecurableDetail>({
    queryKey: key,
    queryFn: () => resolveSecurable(catalog, database, object),
    retry: false,
  })
  const securableId = detail.data?.id
  const lineage = useQuery({
    queryKey: ['catalog-lineage', securityEpoch, securableId],
    queryFn: () => getLineage(securableId!),
    enabled: !!securableId,
    retry: false,
  })
  const [editingComment, setEditingComment] = useState<string | null>(null)
  const [editingOwner, setEditingOwner] = useState<string | null>(null)
  const [column, setColumn] = useState<SecurableSummary | null>(null)

  const update = useMutation({
    mutationFn: (change: () => Promise<SecurableDetail>) => change(),
    onSuccess: (next) => {
      queryClient.setQueryData(key, next)
      setEditingComment(null)
      setEditingOwner(null)
    },
    onError: (error) => toast.error(message(error, 'Could not save metadata')),
  })

  const header = (
    <h2 id={`catalog-metadata-${object}`} className='mb-4 text-xl font-semibold'>
      Business metadata
    </h2>
  )

  if (detail.isLoading) {
    return (
      <section aria-labelledby={`catalog-metadata-${object}`} className='rounded-2xl border border-border bg-surface-2 p-6'>
        {header}
        <LoadingLines rows={3} />
      </section>
    )
  }
  if (detail.error || !detail.data) {
    const notSynced = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <section aria-labelledby={`catalog-metadata-${object}`} className='rounded-2xl border border-border bg-surface-2 p-6'>
        {header}
        <p className='text-sm text-muted-foreground'>
          {notSynced
            ? 'This object is not in the Nova Catalog yet. Its metadata appears after the next catalog sync, or after a security admin refreshes the catalog.'
            : message(detail.error, 'Business metadata could not be loaded.')}
        </p>
      </section>
    )
  }

  const data = detail.data
  const busy = update.isPending
  const commentDraft = editingComment
  const ownerDraft = editingOwner

  return (
    <section aria-labelledby={`catalog-metadata-${object}`} className='rounded-2xl border border-border bg-surface-2 p-6'>
      {header}
      <div className='grid gap-x-6 gap-y-5 md:grid-cols-2'>
        <div className='space-y-1.5'>
          <div className='flex items-center gap-2'>
            <p className='text-sm text-muted-foreground'>Description</p>
            {data.can_edit && commentDraft === null && (
              <Button variant='ghost' size='sm' className='h-7 px-2' onClick={() => setEditingComment(data.comment ?? '')}>
                <Pencil className='me-1 h-3.5 w-3.5' />
                Edit
              </Button>
            )}
          </div>
          {commentDraft !== null ? (
            <div className='space-y-2'>
              <Textarea
                aria-label='Description'
                value={commentDraft}
                maxLength={4096}
                onChange={(event) => setEditingComment(event.target.value)}
              />
              <div className='flex gap-2'>
                <Button
                  size='sm'
                  disabled={busy}
                  onClick={() => update.mutate(() => annotateSecurable(data.id, { comment: commentDraft.trim() || null }))}
                >
                  {busy && <Loader2 className='me-1.5 h-3.5 w-3.5 animate-spin' />}
                  Save
                </Button>
                <Button size='sm' variant='outline' disabled={busy} onClick={() => setEditingComment(null)}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <p className='text-sm whitespace-pre-wrap'>
              {data.comment || <span className='text-muted-foreground'>No description yet</span>}
            </p>
          )}
          {data.engine_comment && (
            <p className='text-xs text-muted-foreground'>Source comment: {data.engine_comment}</p>
          )}
        </div>

        <div className='space-y-1.5'>
          <div className='flex items-center gap-2'>
            <p className='text-sm text-muted-foreground'>Owner</p>
            {data.can_set_owner && ownerDraft === null && (
              <Button variant='ghost' size='sm' className='h-7 px-2' onClick={() => setEditingOwner(data.owner_name ?? '')}>
                <Pencil className='me-1 h-3.5 w-3.5' />
                Edit
              </Button>
            )}
          </div>
          {ownerDraft !== null ? (
            <div className='flex flex-wrap items-center gap-2'>
              <Input
                aria-label='Owner role'
                value={ownerDraft}
                onChange={(event) => setEditingOwner(event.target.value)}
                placeholder='Role name'
                className='h-8 max-w-56 text-sm'
              />
              <Button
                size='sm'
                disabled={busy}
                onClick={() =>
                  update.mutate(() =>
                    annotateSecurable(data.id, ownerDraft.trim()
                      ? { owner_name: ownerDraft.trim(), owner_type: 'role' }
                      : { owner_name: null }),
                  )
                }
              >
                Save
              </Button>
              <Button size='sm' variant='outline' disabled={busy} onClick={() => setEditingOwner(null)}>
                Cancel
              </Button>
            </div>
          ) : (
            <p className='text-sm'>
              {data.owner_name ? (
                <>
                  {data.owner_name}
                  <span className='text-muted-foreground'> · {data.owner_type ?? 'role'}</span>
                </>
              ) : (
                <span className='text-muted-foreground'>No owner assigned</span>
              )}
            </p>
          )}
        </div>

        <div className='space-y-2 md:col-span-2'>
          <p className='text-sm text-muted-foreground'>Tags</p>
          <TagList
            tags={data.tags}
            busy={busy}
            onRemove={data.can_edit ? (tagKey) => update.mutate(() => deleteTag(data.id, tagKey)) : undefined}
          />
          {data.can_edit && (
            <TagInput
              id={`relation-tag-${object}`}
              busy={busy}
              onAdd={(text) => {
                const tag = parseTagInput(text)
                if (tag) update.mutate(() => putTags(data.id, [tag]))
              }}
            />
          )}
        </div>

        <div className='space-y-2'>
          <p className='text-sm text-muted-foreground'>Lineage</p>
          {lineage.isLoading ? (
            <LoadingLines rows={2} />
          ) : lineage.data && (lineage.data.upstream.length || lineage.data.downstream.length) ? (
            <ul className='space-y-1 text-sm'>
              {lineage.data.upstream.map((node) => (
                <li key={`up-${node.id}`} className='flex items-center gap-1.5'>
                  <ArrowUp className='h-3.5 w-3.5 shrink-0 text-muted-foreground' aria-label='Reads from' />
                  <span className='truncate font-mono text-xs'>{node.full_name}</span>
                  {node.status === 'dropped' && <Badge variant='outline'>Dropped</Badge>}
                </li>
              ))}
              {lineage.data.downstream.map((node) => (
                <li key={`down-${node.id}`} className='flex items-center gap-1.5'>
                  <ArrowDown className='h-3.5 w-3.5 shrink-0 text-muted-foreground' aria-label='Feeds' />
                  <span className='truncate font-mono text-xs'>{node.full_name}</span>
                  {node.status === 'dropped' && <Badge variant='outline'>Dropped</Badge>}
                </li>
              ))}
            </ul>
          ) : (
            <p className='text-sm text-muted-foreground'>
              No lineage recorded. Lineage comes from INSERT ... SELECT and CREATE ... AS statements run through Nova.
            </p>
          )}
        </div>

        <div className='space-y-2'>
          <p className='text-sm text-muted-foreground'>Used by</p>
          {data.used_by.length ? (
            <ul className='space-y-1 text-sm'>
              {data.used_by.map((link) => (
                <li key={`${link.owner_type}-${link.owner_id}`}>
                  {link.label}
                  <span className='text-muted-foreground'>
                    {' '}· {link.owner_type === 'semantic_view' ? 'Semantic View' : 'Entity'}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className='text-sm text-muted-foreground'>No Semantic View or entity you can see uses it</p>
          )}
        </div>
      </div>

      {data.columns.length > 0 && (
        <div className='mt-6 border-t border-border pt-4'>
          <h3 className='mb-3 text-base font-semibold'>Column metadata</h3>
          <div className='overflow-x-auto'>
            <table className='w-full text-sm'>
              <thead>
                <tr className='border-b border-border text-left text-muted-foreground'>
                  <th className='pb-2 pr-4 font-medium'>Column</th>
                  <th className='pb-2 pr-4 font-medium'>Classification</th>
                  <th className='pb-2 pr-4 font-medium'>Tags</th>
                  <th className='pb-2 pr-4 font-medium'>Description</th>
                  {data.can_edit && <th className='pb-2 font-medium'><span className='sr-only'>Actions</span></th>}
                </tr>
              </thead>
              <tbody>
                {data.columns.map((item) => (
                  <tr key={item.id} className='border-b border-surface-border align-top'>
                    <td className='py-2 pr-4 font-medium'>{item.column_name}</td>
                    <td className='py-2 pr-4'>
                      {item.classification ? (
                        <Badge variant='secondary'>{item.classification}</Badge>
                      ) : (
                        <span className='text-muted-foreground'>—</span>
                      )}
                    </td>
                    <td className='py-2 pr-4'>
                      {item.tags.length ? <TagList tags={item.tags} /> : <span className='text-muted-foreground'>—</span>}
                    </td>
                    <td className='py-2 pr-4 text-muted-foreground'>{item.comment || item.engine_comment || '—'}</td>
                    {data.can_edit && (
                      <td className='py-2'>
                        <Button
                          variant='ghost'
                          size='sm'
                          className='h-7 px-2'
                          aria-label={`Edit metadata of column ${item.column_name}`}
                          onClick={() => setColumn(item)}
                        >
                          <Pencil className='h-3.5 w-3.5' />
                        </Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {column && (
        <ColumnMetadataDialog
          key={column.id}
          column={column}
          onOpenChange={(open) => {
            if (!open) setColumn(null)
          }}
          onSaved={() => void queryClient.invalidateQueries({ queryKey: key })}
        />
      )}
    </section>
  )
}
