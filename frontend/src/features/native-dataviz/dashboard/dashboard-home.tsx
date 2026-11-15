import { useState } from 'react'
import { LayoutDashboard, Plus, Search } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { WorkspaceSpace } from '@/features/workspaces/explorer/api'
import { useAuthStore } from '@/stores/auth-store'
import { nativeDocuments } from '../documents'
import { useDashboards } from './hooks'

export function DashboardHome({ onOpen }: { onOpen: (entry: { dashboardId: string; workspaceId: string }) => void }) {
  const { entries, spaces, pending, error, refetch } = useDashboards()
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)
  const visible = entries.filter(item => item.title.toLowerCase().includes(search.trim().toLowerCase()))
  return <div className='flex min-h-0 flex-1 flex-col'>
    <div className='flex shrink-0 flex-wrap items-center gap-3 border-b px-4 py-3'>
      <div className='min-w-48 flex-1'>
        <h2 className='type-display text-xl font-semibold'>Dashboards</h2>
        <p className='text-sm text-muted-foreground'>Native dashboards run every query with your current role.</p>
      </div>
      <div className='relative'>
        <Search className='pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground' aria-hidden />
        <Input aria-label='Search dashboards' placeholder='Search dashboards' value={search} onChange={event => setSearch(event.target.value)} className='h-9 w-56 pl-8' />
      </div>
      <Button onClick={() => setCreating(true)}><Plus />New dashboard</Button>
    </div>
    <div className='min-h-0 flex-1 overflow-y-auto p-4'>
      {pending ? <div role='status' aria-label='Loading dashboards' className='grid gap-3 sm:grid-cols-2 xl:grid-cols-3'>
        {Array.from({ length: 6 }, (_, i) => <div key={i} className='h-28 animate-pulse rounded-lg bg-muted/60' />)}
      </div> : error ? <EmptyState variant='error' title='Dashboards are unavailable' description='They could not be loaded with your current access.'
        action={<Button variant='outline' onClick={refetch}>Try again</Button>} />
        : !visible.length ? <EmptyState icon={LayoutDashboard} title={search ? 'No dashboards match your search' : 'No dashboards yet'}
          description={search ? 'Try another name.' : 'Create a dashboard, add a dataset and build charts — or ask Neva to build one for you.'}
          action={!search ? <Button onClick={() => setCreating(true)}><Plus />New dashboard</Button> : undefined} />
          : <ul className='grid gap-3 sm:grid-cols-2 xl:grid-cols-3'>
            {visible.map(item => <li key={item.resource_id}>
              <button type='button' onClick={() => onOpen({ dashboardId: item.resource_id, workspaceId: item.workspace_id })}
                className='flex h-full w-full flex-col gap-2 rounded-lg border bg-card p-4 text-left transition-colors hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'>
                <span className='flex items-start justify-between gap-2'>
                  <span className='type-title line-clamp-2 font-semibold'>{item.title}</span>
                  <Badge variant={item.current_publication_id ? 'secondary' : 'outline'}>{item.current_publication_id ? 'Published' : 'Draft'}</Badge>
                </span>
                <span className='mt-auto text-xs text-muted-foreground'>
                  {item.space.kind === 'personal' ? 'Personal' : item.space.name} · {item.editable ? 'You can edit' : 'View only'} · Updated {new Date(item.updated_at).toLocaleDateString()}
                </span>
              </button>
            </li>)}
          </ul>}
    </div>
    {creating && <CreateDashboard spaces={spaces.filter(space => space.permission !== 'view')} onClose={() => setCreating(false)}
      onCreated={entry => { setCreating(false); refetch(); onOpen(entry) }} />}
  </div>
}

function CreateDashboard({ spaces, onClose, onCreated }: {
  spaces: WorkspaceSpace[]; onClose: () => void; onCreated: (entry: { dashboardId: string; workspaceId: string }) => void
}) {
  const epoch = useAuthStore(state => state.securityEpoch)
  const [title, setTitle] = useState('')
  const [workspaceId, setWorkspaceId] = useState('personal')
  const [busy, setBusy] = useState(false)
  async function create() {
    setBusy(true)
    const dashboardId = crypto.randomUUID()
    try {
      await nativeDocuments(epoch).createDashboard({ workspace_id: workspaceId, definition: {
        schema_version: 2, dashboard_id: dashboardId, title: title.trim(), description: '', columns: 12,
        pages: [{ page_id: crypto.randomUUID(), title: 'Overview', widgets: [] }], filters: [], actions: [],
      } }, crypto.randomUUID())
      onCreated({ dashboardId, workspaceId })
    } catch {
      toast.error('The dashboard could not be created in this workspace.')
    } finally {
      setBusy(false)
    }
  }
  return <Dialog open onOpenChange={open => { if (!open) onClose() }}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>New dashboard</DialogTitle>
        <DialogDescription>Personal dashboards stay private. Dashboards in a shared workspace can be shared with people and roles.</DialogDescription>
      </DialogHeader>
      <div className='space-y-3'>
        <div className='space-y-1'>
          <Label htmlFor='new-dashboard-title'>Name</Label>
          <Input id='new-dashboard-title' autoFocus value={title} maxLength={256} placeholder='Sales performance'
            onChange={event => setTitle(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && title.trim()) void create() }} />
        </div>
        <div className='space-y-1'>
          <Label>Workspace</Label>
          <Select value={workspaceId} onValueChange={setWorkspaceId}>
            <SelectTrigger aria-label='Workspace' className='w-full'><SelectValue /></SelectTrigger>
            <SelectContent>{spaces.map(space => <SelectItem key={space.id} value={space.id}>{space.kind === 'personal' ? 'Personal' : space.name}</SelectItem>)}</SelectContent>
          </Select>
        </div>
      </div>
      <DialogFooter>
        <Button variant='outline' onClick={onClose}>Cancel</Button>
        <Button disabled={!title.trim() || busy} onClick={() => void create()}>{busy ? 'Creating…' : 'Create dashboard'}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
