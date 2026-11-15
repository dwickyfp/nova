import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Trash2, Users } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import type { DashboardDefinition, GrantSummary } from '../contracts'
import { nativeDocuments } from '../documents'

type Permission = GrantSummary['permission']
type Subject = { subject_type: 'user' | 'role'; subject_id: string; preset: Preset; export: boolean }
type Preset = 'viewer' | 'editor' | 'publisher' | 'manager'

const PRESETS: Record<Preset, { label: string; permissions: Permission[] }> = {
  viewer: { label: 'Can view', permissions: ['view', 'interact'] },
  editor: { label: 'Can edit', permissions: ['view', 'interact', 'comment', 'edit'] },
  publisher: { label: 'Can edit and publish', permissions: ['view', 'interact', 'comment', 'edit', 'publish'] },
  manager: { label: 'Can manage', permissions: ['manage'] },
}

function presetOf(permissions: Permission[]): Preset {
  if (permissions.includes('manage')) return 'manager'
  if (permissions.includes('publish')) return 'publisher'
  if (permissions.includes('edit')) return 'editor'
  return 'viewer'
}

function group(grants: GrantSummary[]): Subject[] {
  const map = new Map<string, Permission[]>()
  for (const grant of grants) {
    const key = `${grant.subject_type}:${grant.subject_id}`
    map.set(key, [...(map.get(key) ?? []), grant.permission])
  }
  return [...map.entries()].map(([key, permissions]) => {
    const [subject_type, ...rest] = key.split(':')
    return { subject_type: subject_type as Subject['subject_type'], subject_id: rest.join(':'), preset: presetOf(permissions), export: permissions.includes('export') }
  })
}

/** Grants apply to the dashboard and the charts and datasets it uses; data access still follows each viewer's role. */
export function ShareDialog({ open, onOpenChange, dashboardId, workspaceId, definition }: {
  open: boolean; onOpenChange: (open: boolean) => void; dashboardId: string; workspaceId: string; definition: DashboardDefinition
}) {
  const epoch = useAuthStore(state => state.securityEpoch)
  const documents = nativeDocuments(epoch)
  const personal = workspaceId === 'personal'
  const state = useQuery({
    queryKey: ['native-dataviz', 'grants', epoch, dashboardId],
    queryFn: ({ signal }) => documents.grants('dashboards', dashboardId, { signal }),
    enabled: open && !personal, retry: false,
  })
  const [subjects, setSubjects] = useState<Subject[] | null>(null)
  const current = subjects ?? (state.data ? group(state.data.grants) : [])
  const [kind, setKind] = useState<'role' | 'user'>('role')
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)

  const charts = [...new Set(definition.pages.flatMap(page => (page.widgets ?? []).flatMap(widget =>
    widget.kind === 'chart' && 'chart_ref' in widget ? [widget.chart_ref.chart_id] : [])))]
  const datasets = [...new Set((definition.datasets ?? []).map(item => item.dataset_id))]

  async function save() {
    setSaving(true)
    const grants = current.map(subject => ({ subject_type: subject.subject_type, subject_id: subject.subject_id,
      permissions: [...PRESETS[subject.preset].permissions, ...(subject.export ? ['export' as Permission] : [])] }))
    // Charts and datasets only need view access so a viewer can open the pinned versions.
    const readOnly = grants.map(grant => ({ ...grant, permissions: ['view' as Permission] }))
    try {
      const head = await documents.grants('dashboards', dashboardId)
      await documents.replaceGrants('dashboards', dashboardId, { operation_id: crypto.randomUUID(), expected_revision: head.revision, grants })
      const failures: string[] = []
      for (const [collection, ids] of [['charts', charts], ['datasets', datasets]] as const) {
        for (const id of ids) {
          try {
            const existing = await documents.grants(collection, id)
            const kept = group(existing.grants).filter(subject => !current.some(item => item.subject_type === subject.subject_type && item.subject_id === subject.subject_id))
              .map(subject => ({ subject_type: subject.subject_type, subject_id: subject.subject_id,
                permissions: [...PRESETS[subject.preset].permissions, ...(subject.export ? ['export' as Permission] : [])] }))
            await documents.replaceGrants(collection, id, { operation_id: crypto.randomUUID(), expected_revision: existing.revision, grants: [...kept, ...readOnly] })
          } catch {
            failures.push(id)
          }
        }
      }
      if (failures.length) toast.warning(`Shared the dashboard, but ${failures.length} chart or dataset could not be shared. Viewers may not see those widgets.`)
      else toast.success('Sharing updated.')
      setSubjects(null)
      await state.refetch()
      onOpenChange(false)
    } catch (error) {
      toast.error(error instanceof ApiError && error.status === 409 ? 'Sharing changed in the meantime. Review the current list and save again.' : 'Sharing could not be updated.')
      setSubjects(null)
      await state.refetch()
    } finally {
      setSaving(false)
    }
  }

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className='sm:max-w-lg'>
      <DialogHeader>
        <DialogTitle>Share dashboard</DialogTitle>
        <DialogDescription>Sharing lets people open this dashboard. It does not give access to data: every viewer sees only the rows and columns their own role can read.</DialogDescription>
      </DialogHeader>
      {personal ? <p className='rounded-md border border-dashed p-3 text-sm text-muted-foreground'>
        Dashboards in your personal workspace stay private. Create the dashboard in a shared workspace to share it with people or roles.
      </p> : <>
        <div className='flex gap-2'>
          <Select value={kind} onValueChange={value => setKind(value as 'role' | 'user')}>
            <SelectTrigger aria-label='Share with' className='h-9 w-28'><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value='role'>Role</SelectItem><SelectItem value='user'>User</SelectItem></SelectContent>
          </Select>
          <Input aria-label={kind === 'role' ? 'Role name' : 'User name'} placeholder={kind === 'role' ? 'ANALYST' : 'username'} value={name}
            maxLength={128} className='h-9' onChange={event => setName(event.target.value)} />
          <Button variant='outline' disabled={!name.trim() || current.some(item => item.subject_type === kind && item.subject_id === name.trim())}
            onClick={() => { setSubjects([...current, { subject_type: kind, subject_id: name.trim(), preset: 'viewer', export: false }]); setName('') }}>Add</Button>
        </div>
        <ul aria-label='People and roles with access' className='max-h-64 space-y-1 overflow-y-auto'>
          {state.isPending ? <li role='status' className='text-sm text-muted-foreground'>Loading…</li>
            : !current.length ? <li className='flex items-center gap-2 text-sm text-muted-foreground'><Users className='size-4' />Only you can open this dashboard.</li>
              : current.map((subject, index) => <li key={`${subject.subject_type}:${subject.subject_id}`} className='flex flex-wrap items-center gap-2 rounded-md border px-3 py-2'>
                <span className='min-w-0 flex-1 truncate text-sm'>{subject.subject_id} <span className='text-muted-foreground'>· {subject.subject_type}</span></span>
                <Select value={subject.preset} onValueChange={preset => setSubjects(current.map((item, i) => i === index ? { ...item, preset: preset as Preset } : item))}>
                  <SelectTrigger aria-label={`Access for ${subject.subject_id}`} className='h-8 w-44'><SelectValue /></SelectTrigger>
                  <SelectContent>{(Object.keys(PRESETS) as Preset[]).map(preset => <SelectItem key={preset} value={preset}>{PRESETS[preset].label}</SelectItem>)}</SelectContent>
                </Select>
                <label className='flex items-center gap-1 text-xs'>
                  <input type='checkbox' checked={subject.export} onChange={event => setSubjects(current.map((item, i) => i === index ? { ...item, export: event.target.checked } : item))} />
                  Export
                </label>
                <Button variant='ghost' size='icon' className='size-8' aria-label={`Remove ${subject.subject_id}`}
                  onClick={() => setSubjects(current.filter((_, i) => i !== index))}><Trash2 /></Button>
              </li>)}
        </ul>
      </>}
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>Close</Button>
        {!personal && <Button disabled={saving || subjects === null} onClick={() => void save()}>{saving ? 'Saving…' : 'Save sharing'}</Button>}
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
