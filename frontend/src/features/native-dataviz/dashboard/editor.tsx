import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, BarChart3, Check, Eye, Filter, Hash, History, Loader2, Pencil, Plus, Redo2, Share2, Sparkles,
  Table2, Type, Undo2, Upload,
} from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { ApiError } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import type { ChartDefinition, DashboardDefinition, Operation } from '../contracts'
import { DashboardDataTab } from '../dashboard-data-tab'
import { nativeDocuments } from '../documents'
import { ChartInspector } from '../charts/chart-inspector'
import type { Visual } from '../charts/builder-model'
import type { ValueSelection } from '../charts/native-chart'
import { DashboardCanvas } from './canvas'
import { FilterBar, FilterBuilder } from './filters'
import { chartContext, defaultValue, type CrossFilter, type FilterDefinition, type FilterValue } from './filter-model'
import { firstFreeSlot, sizeFor, type Layout, type Widget } from './layout'
import { NATIVE_DASHBOARD_CHANGED, type NativeDashboardChanged } from './events'
import { applyLocal, inverse, sameLayout, type Change } from './operations'
import { ShareDialog } from './share-dialog'

export type NativeDashboardContext = {
  dashboard_id: string
  revision: number
  page_id: string | null
  selected_widget_id: string | null
}

type Props = {
  dashboardId: string
  workspaceId: string
  onBack: () => void
  /** Bumped by the host when Neva reports a committed change. */
  refreshToken?: number
  onContextChange?: (context: NativeDashboardContext | null) => void
  onAskNeva?: (prompt: string) => void
  /** The Agent panel uses compact chrome around the same editor. */
  density?: 'full' | 'panel'
}

type Status = 'saved' | 'saving' | 'conflict' | 'error'
type Inspector = { mode: 'new'; visual: Visual } | { mode: 'edit'; widget: Widget; chart: ChartDefinition }

function message(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return 'Someone else changed this dashboard. The latest version was loaded; review it and try again.'
    if ([401, 403, 404].includes(error.status)) return 'You no longer have permission for this change.'
    if (error.status === 422) return 'This change is not valid for the dashboard. It was not saved.'
    if (error.status === 503) return 'Saving is paused for maintenance. Your change was not saved.'
  }
  return 'The change could not be saved. Check your connection and try again.'
}

export function DashboardEditor(props: Props) {
  const epoch = useAuthStore(state => state.securityEpoch)
  return <Editor key={`${props.dashboardId}:${epoch}`} {...props} epoch={epoch} />
}

function Editor({ dashboardId, workspaceId, onBack, refreshToken, onContextChange, onAskNeva, density = 'full', epoch }: Props & { epoch: number }) {
  const documents = useMemo(() => nativeDocuments(epoch), [epoch])
  const client = useQueryClient()
  const permissions = useQuery({
    queryKey: ['native-dataviz', 'permissions', epoch, dashboardId],
    queryFn: ({ signal }) => documents.permissions('dashboards', dashboardId, { signal }),
    retry: false,
  })
  const canEdit = permissions.data?.includes('edit') ?? false
  const canPublish = permissions.data?.includes('publish') ?? false
  const canManage = permissions.data?.includes('manage') ?? false
  const snapshot = useQuery({
    queryKey: ['native-dataviz', 'dashboard', epoch, dashboardId, canEdit],
    queryFn: ({ signal }) => documents.read('dashboards', dashboardId, { draft: canEdit, signal }),
    enabled: permissions.isSuccess, retry: false, refetchOnWindowFocus: false,
  })
  const [definition, setDefinition] = useState<DashboardDefinition | null>(null)
  const [revision, setRevision] = useState(0)
  const [publicationId, setPublicationId] = useState<string | null>(null)
  useEffect(() => {
    if (!snapshot.data) return
    setDefinition(snapshot.data.definition as DashboardDefinition)
    setRevision(snapshot.data.revision)
    setPublicationId(snapshot.data.publication_id ?? null)
  }, [snapshot.data])
  const published = useQuery({
    queryKey: ['native-dataviz', 'dashboard-publication', epoch, dashboardId, publicationId],
    queryFn: ({ signal }) => documents.read('dashboards', dashboardId, { draft: false, signal }),
    enabled: Boolean(publicationId) && canEdit, retry: false,
  })

  const [tab, setTab] = useState<'canvas' | 'data'>('canvas')
  const [editing, setEditing] = useState(false)
  const [pageId, setPageId] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [status, setStatus] = useState<Status>('saved')
  const [inspector, setInspector] = useState<Inspector | null>(null)
  const [inspectorError, setInspectorError] = useState<string>()
  const [savingChart, setSavingChart] = useState(false)
  const [filterValues, setFilterValues] = useState<Record<string, FilterValue>>({})
  const [crossFilters, setCrossFilters] = useState<CrossFilter[]>([])
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [publishOpen, setPublishOpen] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [textOpen, setTextOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [undo, setUndo] = useState<{ forward: Change[]; backward: Change[] }[]>([])
  const [redo, setRedo] = useState<{ forward: Change[]; backward: Change[] }[]>([])
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  const lastSequence = useRef(0)
  const revisionRef = useRef(revision)
  useEffect(() => { revisionRef.current = revision }, [revision])

  const datasets = useQuery({
    queryKey: ['native-dataviz', 'dashboard-datasets', epoch, dashboardId, revision],
    queryFn: ({ signal }) => documents.dashboardDatasets(dashboardId, revision, { signal }),
    enabled: canEdit && revision > 0, retry: false,
  })

  const page = definition?.pages.find(item => item.page_id === pageId) ?? definition?.pages[0]
  useEffect(() => {
    if (definition && !definition.pages.some(item => item.page_id === pageId)) setPageId(definition.pages[0]?.page_id ?? null)
  }, [definition, pageId])

  // Default filter values apply once per loaded definition.
  const filterSignature = JSON.stringify(definition?.filters ?? [])
  useEffect(() => {
    if (!definition) return
    setFilterValues(current => {
      const next: Record<string, FilterValue> = {}
      for (const filter of definition.filters ?? []) {
        const value = filter.filter_id in current ? current[filter.filter_id] : defaultValue(filter)
        if (value) next[filter.filter_id] = value
      }
      return next
    })
  }, [filterSignature])

  useEffect(() => {
    onContextChange?.(definition && page ? { dashboard_id: dashboardId, revision, page_id: page.page_id, selected_widget_id: selected } : null)
  }, [onContextChange, dashboardId, revision, page, selected, definition])
  useEffect(() => () => onContextChange?.(null), [onContextChange])

  const reload = useCallback(async () => {
    const result = await snapshot.refetch()
    if (result.data) {
      setDefinition(result.data.definition as DashboardDefinition)
      setRevision(result.data.revision)
      setPublicationId(result.data.publication_id ?? null)
    }
  }, [snapshot])

  // Changes by Neva or another editor arrive as committed document events.
  useEffect(() => { if (refreshToken) void reload() }, [refreshToken])
  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<NativeDashboardChanged>).detail
      if (detail?.dashboard_id?.toLowerCase() === dashboardId.toLowerCase() && detail.revision !== revisionRef.current) {
        void reload()
        void datasets.refetch()
      }
    }
    window.addEventListener(NATIVE_DASHBOARD_CHANGED, listener)
    return () => window.removeEventListener(NATIVE_DASHBOARD_CHANGED, listener)
  }, [dashboardId, reload, datasets])
  useEffect(() => {
    if (!canEdit) return
    let stopped = false
    const poll = async () => {
      try {
        const page = await documents.events('dashboards', dashboardId, { after: lastSequence.current, limit: 100 })
        const first = lastSequence.current === 0
        lastSequence.current = page.next_sequence
        if (!first && page.events.length && status !== 'saving') {
          const latest = await documents.read('dashboards', dashboardId, { draft: true })
          if (!stopped && latest.revision !== revisionRef.current) {
            setDefinition(latest.definition as DashboardDefinition)
            setRevision(latest.revision)
            setPublicationId(latest.publication_id ?? null)
          }
        }
      } catch { /* the next poll retries; editing state is unaffected */ }
    }
    void poll()
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void poll() }, 4000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [canEdit, dashboardId, documents, status])

  const apply = useCallback((changes: Change[], options: { record?: boolean; label?: string } = {}) => {
    const run = async () => {
      const before = definitionRef.current
      if (!before) return false
      const operation: Operation = { operation_id: crypto.randomUUID(), expected_revision: revisionRef.current, changes }
      setDefinition(changes.reduce(applyLocal, before))
      setStatus('saving')
      try {
        const receipt = await documents.apply(dashboardId, operation)
        setRevision(receipt.new_revision)
        revisionRef.current = receipt.new_revision
        lastSequence.current = Math.max(lastSequence.current, receipt.event_sequence)
        setStatus('saved')
        if (options.record !== false) {
          const backward = changes.map(change => inverse(change, before)).filter((item): item is Change => item !== null).reverse()
          if (backward.length === changes.length) {
            setUndo(stack => [...stack.slice(-49), { forward: changes, backward }])
            setRedo([])
          }
        }
        return true
      } catch (error) {
        setDefinition(before)
        if (error instanceof ApiError && error.status === 409) {
          setStatus('conflict')
          await reload()
        } else setStatus('error')
        toast.error(message(error))
        return false
      }
    }
    const next = queue.current.then(run, run)
    queue.current = next
    return next
  }, [dashboardId, documents, reload])
  const definitionRef = useRef(definition)
  useEffect(() => { definitionRef.current = definition }, [definition])

  const undoLast = () => {
    const entry = undo[undo.length - 1]
    if (!entry) return
    void apply(entry.backward, { record: false }).then(ok => {
      if (ok) { setUndo(stack => stack.slice(0, -1)); setRedo(stack => [...stack, entry]) }
    })
  }
  const redoLast = () => {
    const entry = redo[redo.length - 1]
    if (!entry) return
    void apply(entry.forward, { record: false }).then(ok => {
      if (ok) { setRedo(stack => stack.slice(0, -1)); setUndo(stack => [...stack, entry]) }
    })
  }
  useEffect(() => {
    if (!editing) return
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.closest('input, textarea, [contenteditable="true"], .monaco-editor')) return
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) redoLast(); else undoLast()
      }
      if (event.key === 'Escape') { setSelected(null) }
      if ((event.key === 'Delete' || event.key === 'Backspace') && selected && page) {
        const widget = page.widgets?.find(item => item.widget_id === selected)
        if (widget) { event.preventDefault(); void apply([{ op: 'widget.remove', page_id: page.page_id, widget_id: widget.widget_id }]); setSelected(null) }
      }
      if (selected && page && event.altKey && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
        event.preventDefault()
        const widgets = page.widgets ?? []
        const moved = widgets.map(widget => {
          if (widget.widget_id !== selected) return { widget_id: widget.widget_id, layout: widget.layout }
          const l = widget.layout
          const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key]!
          return { widget_id: widget.widget_id, layout: event.shiftKey
            ? { ...l, w: Math.max(2, Math.min(12 - l.x, l.w + delta[0])), h: Math.max(2, l.h + delta[1]) }
            : { ...l, x: Math.max(0, Math.min(12 - l.w, l.x + delta[0])), y: Math.max(0, l.y + delta[1]) } }
        })
        const target = moved.find(item => item.widget_id === selected)!.layout
        const collides = moved.some(item => item.widget_id !== selected && item.layout.x < target.x + target.w && target.x < item.layout.x + item.layout.w
          && item.layout.y < target.y + target.h && target.y < item.layout.y + item.layout.h)
        if (!collides) void apply([{ op: 'layout.apply', page_id: page.page_id, placements: moved }])
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  })

  const onLayout = useCallback((placements: { widget_id: string; layout: Layout }[]) => {
    const current = definitionRef.current?.pages.find(item => item.page_id === pageIdRef.current) ?? definitionRef.current?.pages[0]
    if (!current || sameLayout(current, placements)) return
    void apply([{ op: 'layout.apply', page_id: current.page_id, placements }])
  }, [apply])
  const pageIdRef = useRef(pageId)
  useEffect(() => { pageIdRef.current = page?.page_id ?? null }, [page])

  async function saveChart(chart: ChartDefinition) {
    if (!page) return
    setSavingChart(true)
    setInspectorError(undefined)
    try {
      if (inspector?.mode === 'edit') {
        const head = await documents.read('charts', chart.chart_id, { draft: true })
        const receipt = await documents.replaceChart(chart, { operation_id: crypto.randomUUID(), expected_revision: head.revision })
        const widget = { ...inspector.widget, chart_ref: { chart_id: chart.chart_id, revision: receipt.new_revision } } as Widget
        if (await apply([{ op: 'widget.replace', page_id: page.page_id, widget }])) setInspector(null)
      } else {
        await documents.createChart({ workspace_id: workspaceId, definition: chart }, crypto.randomUUID())
        const size = sizeFor(chart.visual)
        const widget = { kind: 'chart', widget_id: crypto.randomUUID(), title: '', locked: false,
          layout: firstFreeSlot(page.widgets ?? [], size.w, size.h), chart_ref: { chart_id: chart.chart_id, revision: 1 } } as Widget
        if (await apply([{ op: 'widget.add', page_id: page.page_id, widget }])) {
          setInspector(null)
          setSelected(widget.widget_id)
        }
      }
      await client.invalidateQueries({ queryKey: ['native-dataviz', 'chart', epoch, chart.chart_id] })
    } catch (error) {
      setInspectorError(message(error))
    } finally {
      setSavingChart(false)
    }
  }

  function addText(text: string, style: 'title' | 'body') {
    if (!page) return
    const widget = { kind: 'text', widget_id: crypto.randomUUID(), title: '', locked: false, text, style,
      layout: firstFreeSlot(page.widgets ?? [], 12, style === 'title' ? 2 : 2) } as Widget
    void apply([{ op: 'widget.add', page_id: page.page_id, widget }])
  }

  function duplicate(widget: Widget) {
    if (!page) return
    const copy = { ...widget, widget_id: crypto.randomUUID(), layout: firstFreeSlot(page.widgets ?? [], widget.layout.w, widget.layout.h) } as Widget
    void apply([{ op: 'widget.add', page_id: page.page_id, widget: copy }])
  }

  function addPage() {
    if (!definition) return
    const created = { page_id: crypto.randomUUID(), title: `Page ${definition.pages.length + 1}`, widgets: [] }
    void apply([{ op: 'page.add', page: created }]).then(ok => { if (ok) setPageId(created.page_id) })
  }

  const contextFor = useCallback((widget: Widget, chart: ChartDefinition) => definition && page
    ? chartContext({ definition, chart, widgetId: widget.widget_id, pageId: page.page_id, values: filterValues, crossFilters })
    : undefined, [definition, page, filterValues, crossFilters])

  const onSelectValue = useCallback((widget: Widget, _chart: ChartDefinition, selection: ValueSelection) => {
    const chart = _chart
    setCrossFilters(current => {
      const exists = current.findIndex(item => item.source_widget_id === widget.widget_id
        && JSON.stringify(item.value) === JSON.stringify(selection.value))
      if (exists >= 0) return current.filter((_, index) => index !== exists)
      const others = current.filter(item => item.source_widget_id !== widget.widget_id)
      return [...others, { source_widget_id: widget.widget_id, label: selection.label, value: selection.value,
        field: { dataset_id: chart.dataset_id, dataset_revision: chart.dataset_revision, field_id: selection.field_id } }]
    })
  }, [])

  if (permissions.isError || snapshot.isError) {
    return <div className='flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center'>
      <p className='font-medium'>This dashboard is unavailable</p>
      <p className='max-w-sm text-sm text-muted-foreground'>It may have been removed, or your current role does not have access.</p>
      <Button variant='outline' onClick={onBack}><ArrowLeft />Back to dashboards</Button>
    </div>
  }
  if (!definition || !page) return <div role='status' aria-label='Loading dashboard' className='flex flex-1 items-center justify-center'>
    <Loader2 className='size-5 animate-spin text-muted-foreground' />
  </div>

  const sources = datasets.data?.datasets ?? []
  const publishedRevision = published.data?.revision
  const state = !publicationId ? 'Draft' : publishedRevision === revision ? 'Published' : 'Unpublished changes'
  const compact = density === 'panel'

  return <div className='flex min-h-0 min-w-0 flex-1 flex-col bg-background'>
    <header className={cn('flex shrink-0 flex-wrap items-center gap-2 border-b', compact ? 'px-3 py-2' : 'px-4 py-3')}>
      {!compact && <Button variant='ghost' size='icon' className='size-9' aria-label='Back to dashboards' onClick={onBack}><ArrowLeft /></Button>}
      <div className='min-w-0 flex-1'>
        {renaming && canEdit ? <Input autoFocus defaultValue={definition.title} maxLength={256} aria-label='Dashboard title'
          className='h-9 max-w-md text-lg font-semibold'
          onBlur={event => {
            setRenaming(false)
            const title = event.target.value.trim()
            if (title && title !== definition.title) void apply([{ op: 'dashboard.rename', title }])
          }}
          onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); if (event.key === 'Escape') setRenaming(false) }} />
          : <h2 className={cn('type-display truncate font-semibold', compact ? 'text-base' : 'text-xl')}
            onDoubleClick={() => canEdit && setRenaming(true)}>{definition.title}</h2>}
        <p className='flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground'>
          <span>Revision {revision}</span>
          <span aria-live='polite' className={cn(status === 'conflict' || status === 'error' ? 'text-destructive' : '')}>
            {status === 'saving' ? 'Saving…' : status === 'conflict' ? 'Reloaded after a conflict' : status === 'error' ? 'Last change not saved' : 'All changes saved'}
          </span>
        </p>
      </div>
      <Badge variant={state === 'Published' ? 'secondary' : 'outline'} className='h-7'>{state}</Badge>
      {onAskNeva && <Button variant='outline' size='sm' className='h-9' onClick={() => onAskNeva(`Help me improve the dashboard “${definition.title}”.`)}>
        <Sparkles />Ask Neva
      </Button>}
      {canEdit && <Button variant={editing ? 'default' : 'outline'} size='sm' className='h-9' onClick={() => { setEditing(!editing); setSelected(null); setInspector(null) }}>
        {editing ? <><Check />Done</> : <><Pencil />Edit</>}
      </Button>}
      {canEdit && <Tooltip><TooltipTrigger asChild><Button variant='ghost' size='icon' className='size-9' aria-label='Version history' onClick={() => setHistoryOpen(true)}><History /></Button></TooltipTrigger><TooltipContent>Version history</TooltipContent></Tooltip>}
      {canManage && <Button variant='outline' size='sm' className='h-9' onClick={() => setShareOpen(true)}><Share2 />Share</Button>}
      {canPublish && <Button size='sm' className='h-9' disabled={state === 'Published' || status === 'saving'} onClick={() => setPublishOpen(true)}><Upload />Publish</Button>}
    </header>

    <div className='flex shrink-0 flex-wrap items-center gap-2 border-b px-4 py-1.5'>
      {canEdit && <Tabs value={tab} onValueChange={value => setTab(value as 'canvas' | 'data')}>
        <TabsList><TabsTrigger value='canvas'>Canvas</TabsTrigger><TabsTrigger value='data'>Data</TabsTrigger></TabsList>
      </Tabs>}
      {tab === 'canvas' && <nav aria-label='Dashboard pages' className='flex min-w-0 flex-1 items-center gap-1 overflow-x-auto'>
        {definition.pages.map(item => <Button key={item.page_id} variant={item.page_id === page.page_id ? 'secondary' : 'ghost'} size='sm'
          aria-current={item.page_id === page.page_id ? 'page' : undefined} onClick={() => { setPageId(item.page_id); setSelected(null) }}>{item.title}</Button>)}
        {editing && definition.pages.length < 15 && <Button variant='ghost' size='icon' className='size-8' aria-label='Add page' onClick={addPage}><Plus /></Button>}
      </nav>}
      {tab === 'canvas' && editing && <div role='toolbar' aria-label='Add to dashboard' className='flex flex-wrap items-center gap-1'>
        <Button variant='outline' size='sm' onClick={() => setInspector({ mode: 'new', visual: 'bar' })}><BarChart3 />Chart</Button>
        <Button variant='outline' size='sm' onClick={() => setInspector({ mode: 'new', visual: 'kpi' })}><Hash />KPI</Button>
        <Button variant='outline' size='sm' onClick={() => setInspector({ mode: 'new', visual: 'table' })}><Table2 />Table</Button>
        <Button variant='outline' size='sm' onClick={() => setTextOpen(true)}><Type />Text</Button>
        <Button variant='outline' size='sm' onClick={() => setFiltersOpen(true)}><Filter />Filters</Button>
        <span className='mx-1 h-6 w-px bg-border' aria-hidden />
        <Button variant='ghost' size='icon' className='size-8' aria-label='Undo' disabled={!undo.length || status === 'saving'} onClick={undoLast}><Undo2 /></Button>
        <Button variant='ghost' size='icon' className='size-8' aria-label='Redo' disabled={!redo.length || status === 'saving'} onClick={redoLast}><Redo2 /></Button>
      </div>}
      {tab === 'canvas' && !editing && canEdit && <span className='ms-auto flex items-center gap-1 text-xs text-muted-foreground'><Eye className='size-3.5' />Viewing your draft</span>}
    </div>

    {tab === 'data' && canEdit ? <DashboardDataTab dashboardId={dashboardId} workspaceId={workspaceId} revision={revision}
      onCommitted={receipt => { setRevision(receipt.new_revision); void reload(); void datasets.refetch() }} onReload={() => void reload()} />
      : <div className='flex min-h-0 flex-1'>
        <div className='flex min-h-0 min-w-0 flex-1 flex-col'>
          <FilterBar definition={definition} values={filterValues} draft={editing}
            onChange={(id, value) => setFilterValues(current => {
              const next = { ...current }
              if (value) next[id] = value; else delete next[id]
              return next
            })}
            crossFilters={crossFilters} onClearCross={index => setCrossFilters(index === null ? [] : crossFilters.filter((_, i) => i !== index))}
            editing={editing} onEdit={() => setFiltersOpen(true)} />
          <DashboardCanvas definition={definition} page={page} draft={editing} editing={editing}
            selectedId={selected} onSelect={setSelected} onLayout={onLayout}
            onEditChart={(widget, chart) => setInspector({ mode: 'edit', widget, chart })}
            onDuplicate={duplicate} onRemove={widget => void apply([{ op: 'widget.remove', page_id: page.page_id, widget_id: widget.widget_id }])}
            onAskNeva={onAskNeva ? (_widget, chart) => onAskNeva(chart ? `Explain the chart “${chart.title}” on this dashboard and suggest improvements.` : 'Help me with this widget.') : undefined}
            contextFor={contextFor} onSelectValue={onSelectValue}
            selectedValueFor={widget => crossFilters.find(item => item.source_widget_id === widget.widget_id)?.label ?? null} />
        </div>
        {inspector && <div className={cn('flex min-h-0 shrink-0 border-l', compact ? 'w-full max-w-sm' : 'w-[360px] max-w-[45vw]')}>
          <ChartInspector key={inspector.mode === 'edit' ? inspector.chart.chart_id : `new-${inspector.visual}`}
            datasets={sources} chart={inspector.mode === 'edit' ? inspector.chart : null} initialVisual={inspector.mode === 'new' ? inspector.visual : undefined}
            saving={savingChart} error={inspectorError} onSave={chart => void saveChart(chart)} onClose={() => { setInspector(null); setInspectorError(undefined) }} />
        </div>}
      </div>}

    {filtersOpen && <FilterBuilder open onOpenChange={setFiltersOpen} definition={definition} datasets={sources} saving={status === 'saving'}
      onApply={(filters: FilterDefinition[]) => void apply([{ op: 'filters.replace', filters }]).then(ok => { if (ok) setFiltersOpen(false) })} />}
    <PublishDialog open={publishOpen} onOpenChange={setPublishOpen} onPublish={async notes => {
      try {
        await documents.publish('dashboards', dashboardId, { operation_id: crypto.randomUUID(), expected_revision: revision, release_notes: notes })
        toast.success('Dashboard published. Viewers now see this version.')
        setPublishOpen(false)
        await reload()
        await published.refetch()
      } catch (error) { toast.error(message(error)) }
    }} />
    {shareOpen && <ShareDialog open onOpenChange={setShareOpen} dashboardId={dashboardId} workspaceId={workspaceId} definition={definition} />}
    <HistorySheet open={historyOpen} onOpenChange={setHistoryOpen} dashboardId={dashboardId} epoch={epoch} />
    <TextDialog open={textOpen} onOpenChange={setTextOpen} onAdd={addText} />
  </div>
}

function PublishDialog({ open, onOpenChange, onPublish }: { open: boolean; onOpenChange: (open: boolean) => void; onPublish: (notes: string) => Promise<void> }) {
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Publish dashboard</DialogTitle>
        <DialogDescription>Viewers will see this exact version, including the chart and dataset versions it uses. Later edits stay in your draft until you publish again. Each viewer still sees only the data their own role can read.</DialogDescription>
      </DialogHeader>
      <div className='space-y-1'>
        <Label htmlFor='publish-notes'>What changed (optional)</Label>
        <Textarea id='publish-notes' value={notes} maxLength={2000} onChange={event => setNotes(event.target.value)} />
      </div>
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button disabled={busy} onClick={async () => { setBusy(true); try { await onPublish(notes) } finally { setBusy(false) } }}>{busy ? 'Publishing…' : 'Publish'}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}

function TextDialog({ open, onOpenChange, onAdd }: { open: boolean; onOpenChange: (open: boolean) => void; onAdd: (text: string, style: 'title' | 'body') => void }) {
  const [text, setText] = useState('')
  const [style, setStyle] = useState<'title' | 'body'>('body')
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent>
      <DialogHeader><DialogTitle>Add text</DialogTitle><DialogDescription>Text is shown exactly as written; formatting markup is not interpreted.</DialogDescription></DialogHeader>
      <div className='flex gap-2'>
        <Button variant={style === 'title' ? 'secondary' : 'outline'} size='sm' onClick={() => setStyle('title')}>Heading</Button>
        <Button variant={style === 'body' ? 'secondary' : 'outline'} size='sm' onClick={() => setStyle('body')}>Paragraph</Button>
      </div>
      <Textarea aria-label='Text' value={text} maxLength={8000} rows={4} onChange={event => setText(event.target.value)} />
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button disabled={!text.trim()} onClick={() => { onAdd(text.trim(), style); setText(''); onOpenChange(false) }}>Add text</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}

function HistorySheet({ open, onOpenChange, dashboardId, epoch }: { open: boolean; onOpenChange: (open: boolean) => void; dashboardId: string; epoch: number }) {
  const history = useQuery({
    queryKey: ['native-dataviz', 'history', epoch, dashboardId],
    queryFn: ({ signal }) => nativeDocuments(epoch).history('dashboards', dashboardId, { limit: 50, signal }),
    enabled: open, retry: false,
  })
  return <Sheet open={open} onOpenChange={onOpenChange}>
    <SheetContent>
      <SheetHeader><SheetTitle>Version history</SheetTitle><SheetDescription>Every saved change creates a revision. Published versions stay unchanged.</SheetDescription></SheetHeader>
      <ol className='space-y-1 overflow-y-auto px-4 pb-4'>
        {history.isPending ? <li role='status' className='text-sm text-muted-foreground'>Loading…</li>
          : history.isError ? <li role='alert' className='text-sm text-muted-foreground'>History is unavailable.</li>
            : history.data.map(item => <li key={item.revision} className='flex items-center justify-between rounded-md border px-3 py-2 text-sm'>
              <span>Revision {item.revision} <span className='text-muted-foreground'>· {item.author_name}</span></span>
              <time className='text-xs text-muted-foreground' dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time>
            </li>)}
      </ol>
    </SheetContent>
  </Sheet>
}
