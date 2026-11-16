import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, BarChart3, CalendarClock, Check, Download, FileSpreadsheet, Filter, Hash, History, Loader2, Pencil, Plus,
  MessageSquare, Printer, Redo2, Share2, Sparkles, Table2, Type, Undo2, Upload,
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
import type { ChartDefinition, DashboardDefinition, FilterTree, Interaction, Operation } from '../contracts'
import { DashboardDataTab } from '../dashboard-data-tab'
import { nativeDocuments } from '../documents'
import { ChartInspector } from '../charts/chart-inspector'
import type { Visual } from '../charts/builder-model'
import type { ValueSelection } from '../charts/native-chart'
import { DashboardCanvas } from './canvas'
import { LinkDialog } from './link-dialog'
import {
  authorizeExport, chartImage, fileName, hasImage, printHtml, reportHtml, resultTable, saveFile, toCsv, toXlsx,
  type ChartFileFormat, type ReportItem, type ResultTable,
} from './export'
import { chartDocumentQuery } from './hooks'
import { chartDataQuery, chartRequest } from '../charts/chart-data'
import { printImage } from '../charts/print-image'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { readToken } from '@/lib/read-token'
import { FilterBar, FilterBuilder } from './filters'
import { describeFilterValue } from './filter-describe'
import { chartContext, defaultValue, type CrossFilter, type FilterDefinition, type FilterValue } from './filter-model'
import { firstFreeSlot, sizeFor, type Layout, type Widget } from './layout'
import { NATIVE_DASHBOARD_CHANGED, NATIVE_DASHBOARD_OPEN, type LinkedSelection, type NativeDashboardChanged, type NativeDashboardOpen } from './events'
import { applyLocal, inverse, sameLayout, type Change } from './operations'
import { ShareDialog } from './share-dialog'
import { ScheduleDialog } from './schedule-dialog'
import { CommentsPanel } from './comments-panel'
import { useComments } from './comments'
import { usePresence } from './presence'
import { PresenceAvatars } from './presence-ui'

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
  /** Values carried in by a link from another dashboard. */
  initialSelections?: LinkedSelection[]
}

/** Selections carried in by a dashboard link belong to no widget here. */
const LINKED_SOURCE = 'linked'

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

function Editor({ dashboardId, workspaceId, onBack, refreshToken, onContextChange, onAskNeva, density = 'full', initialSelections, epoch }: Props & { epoch: number }) {
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
  const canExport = permissions.data?.includes('export') ?? false
  // Interact covers filters, selections, drill-down and links; editing implies it.
  const canInteract = canEdit || (permissions.data?.includes('interact') ?? false)
  const canComment = canEdit || (permissions.data?.includes('comment') ?? false)
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
  const [crossFilters, setCrossFilters] = useState<CrossFilter[]>(() =>
    (initialSelections ?? []).map(item => ({ ...item, source_widget_id: LINKED_SOURCE })))
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [publishOpen, setPublishOpen] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const [scheduleOpen, setScheduleOpen] = useState(false)
  const [commentsOpen, setCommentsOpen] = useState(false)
  // Screen readers hear where a keyboard move or resize put the widget.
  const [announcement, setAnnouncement] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [textOpen, setTextOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [undo, setUndo] = useState<{ forward: Change[]; backward: Change[] }[]>([])
  const [redo, setRedo] = useState<{ forward: Change[]; backward: Change[] }[]>([])
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const [bodyWidth, setBodyWidth] = useState(1280)
  useEffect(() => {
    const node = bodyRef.current
    if (!node || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => setBodyWidth(node.clientWidth))
    observer.observe(node)
    setBodyWidth(node.clientWidth)
    return () => observer.disconnect()
  }, [tab, definition === null])
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
  const presence = usePresence({ dashboardId, pageId: page?.page_id ?? null, editing })
  const openComments = (useComments(dashboardId).data ?? []).filter(item => !item.resolved_at).length

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
    if (!result.data) return undefined
    setDefinition(result.data.definition as DashboardDefinition)
    setRevision(result.data.revision)
    setPublicationId(result.data.publication_id ?? null)
    return result.data.definition as DashboardDefinition
  }, [snapshot])

  // Changes by Neva or another editor arrive as committed document events.
  useEffect(() => { if (refreshToken) void reload() }, [refreshToken])
  const [nevaChanged, setNevaChanged] = useState<string[]>([])
  useEffect(() => {
    if (!nevaChanged.length) return
    const timer = window.setTimeout(() => setNevaChanged([]), 4000)
    return () => window.clearTimeout(timer)
  }, [nevaChanged])
  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<NativeDashboardChanged>).detail
      if (detail?.dashboard_id?.toLowerCase() === dashboardId.toLowerCase() && detail.revision !== revisionRef.current) {
        const before = definitionRef.current
        void reload().then(after => {
          // Point at what Neva changed: new widgets and widgets whose content or place moved.
          if (!before || !after) return
          const previous = new Map(before.pages.flatMap(item => item.widgets ?? []).map(widget => [widget.widget_id, JSON.stringify(widget)]))
          setNevaChanged(after.pages.flatMap(item => item.widgets ?? [])
            .filter(widget => previous.get(widget.widget_id) !== JSON.stringify(widget)).map(widget => widget.widget_id))
        })
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
        if (collides) {
          setAnnouncement('Another widget is in the way')
        } else {
          void apply([{ op: 'layout.apply', page_id: page.page_id, placements: moved }])
          setAnnouncement(`${event.shiftKey ? 'Resized' : 'Moved'}: column ${target.x + 1}, row ${target.y + 1}, ${target.w} columns wide, ${target.h} rows tall`)
        }
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

  // Rapid filter clicks settle before widgets re-query.
  const [appliedValues, setAppliedValues] = useState(filterValues)
  useEffect(() => {
    const timer = window.setTimeout(() => setAppliedValues(filterValues), 300)
    return () => window.clearTimeout(timer)
  }, [filterValues])
  const contextFor = useCallback((widget: Widget, chart: ChartDefinition) => definition && page
    ? chartContext({ definition, chart, widgetId: widget.widget_id, pageId: page.page_id, values: appliedValues, crossFilters })
    : undefined, [definition, page, appliedValues, crossFilters])

  const onSelectValue = useCallback((widget: Widget, _chart: ChartDefinition, selection: ValueSelection) => {
    const chart = _chart
    setCrossFilters(current => {
      const same = (item: CrossFilter) => item.source_widget_id === widget.widget_id
        && item.field.field_id === selection.field_id && JSON.stringify(item.value) === JSON.stringify(selection.value)
      // Clicking a selected value again clears it; Shift/Ctrl/Cmd adds values on the same field.
      if (current.some(same)) return current.filter(item => !same(item))
      const others = current.filter(item => item.source_widget_id !== widget.widget_id
        || (selection.additive && item.field.field_id === selection.field_id))
      return [...others, { source_widget_id: widget.widget_id, label: selection.label, value: selection.value,
        ...(selection.period ? { period: selection.period } : {}),
        field: { dataset_id: chart.dataset_id, dataset_revision: chart.dataset_revision, field_id: selection.field_id } }]
    })
  }, [])

  // Removing a chart also removes its links, in one change, so Undo restores both.
  const removal = (widget: Widget): Change[] => {
    const remove: Change = { op: 'widget.remove', page_id: page?.page_id ?? '', widget_id: widget.widget_id }
    const actions = definition?.actions ?? []
    const kept = actions.filter(action => action.source_widget_id !== widget.widget_id)
    return kept.length === actions.length ? [remove] : [{ op: 'actions.replace', actions: kept }, remove]
  }
  const [linking, setLinking] = useState<{ widget: Widget; chart: ChartDefinition } | null>(null)
  const [savingLink, setSavingLink] = useState(false)
  const linksFor = useCallback((widget: Widget) => (definition?.actions ?? [])
    .filter(action => action.source_widget_id === widget.widget_id && action.target_dashboard_id), [definition])
  const openLink = useCallback((link: Interaction, selection: ValueSelection) => {
    const mapping = link.mappings?.find(item => item.source.field_id === selection.field_id)
    if (!mapping || !link.target_dashboard_id || !link.target_workspace_id) return
    // The other dashboard opens under the viewer's own access; a dashboard they
    // cannot open shows its usual "unavailable" state.
    window.dispatchEvent(new CustomEvent<NativeDashboardOpen>(NATIVE_DASHBOARD_OPEN, { detail: {
      dashboardId: link.target_dashboard_id, workspaceId: link.target_workspace_id,
      selections: [{ field: mapping.target, value: selection.value, label: selection.label,
        ...(selection.period ? { period: selection.period } : {}) }],
    } }))
  }, [])
  const saveLink = async (link: Interaction | null) => {
    if (!definition || !linking) return
    setSavingLink(true)
    const others = (definition.actions ?? []).filter(action =>
      !(action.source_widget_id === linking.widget.widget_id && action.target_dashboard_id))
    const ok = await apply([{ op: 'actions.replace', actions: link ? [...others, link] : others }])
    setSavingLink(false)
    if (ok) setLinking(null)
  }
  const [exporting, setExporting] = useState(false)
  const exportFailed = (error: unknown) => toast.error(error instanceof ApiError && error.status === 403
    ? 'Your access to this dashboard does not include downloading it.'
    : 'The download could not be made. Try again in a moment.')
  const downloadChart = useCallback(async (chart: ChartDefinition, context: FilterTree | undefined,
    format: ChartFileFormat, root: HTMLElement) => {
    try {
      await authorizeExport(dashboardId, format, chart.chart_id)
      if (format === 'png') {
        const image = await chartImage(root, {
          title: chart.title, background: readToken('--card', '#ffffff'),
          color: readToken('--foreground', '#111111'), font: getComputedStyle(root).fontFamily,
        })
        if (!image) throw new Error('The chart has no image yet')
        saveFile(image, fileName(chart.title, 'png'))
        return
      }
      const request = chartRequest({ chart, draft: editing, context })
      if (!request) throw new Error('The chart cannot run')
      const table = resultTable(await client.fetchQuery(chartDataQuery(epoch, chart, request)))
      saveFile(format === 'csv' ? toCsv(table) : await toXlsx([{ title: chart.title, table }]),
        fileName(chart.title, format))
    } catch (error) {
      exportFailed(error)
    }
  }, [client, dashboardId, editing, epoch])
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
  const downloadWorkbook = async () => {
    if (!definition || !page) return
    setExporting(true)
    try {
      await authorizeExport(dashboardId, 'xlsx')
      const sheets: { title: string; table: ResultTable }[] = []
      for (const widget of page.widgets ?? []) {
        if (widget.kind !== 'chart' || !('chart_ref' in widget)) continue
        const chart = await client.fetchQuery(chartDocumentQuery(epoch, widget.chart_ref.chart_id, widget.chart_ref.revision, editing))
        const request = chartRequest({ chart, draft: editing, context: contextFor(widget, chart) })
        if (!request) continue
        const result = await client.fetchQuery(chartDataQuery(epoch, chart, request))
        sheets.push({ title: widget.title || chart.title, table: resultTable(result) })
      }
      if (!sheets.length) toast.info('This page has no charts to download yet.')
      else saveFile(await toXlsx(sheets), fileName(`${definition.title} - ${page.title}`, 'xlsx'))
    } catch (error) {
      exportFailed(error)
    } finally {
      setExporting(false)
    }
  }
  const printDashboard = async () => {
    if (!definition || !page) return
    setExporting(true)
    try {
      await authorizeExport(dashboardId, 'pdf')
      const items: ReportItem[] = []
      const many = definition.pages.length > 1
      for (const sheet of definition.pages) {
      if (many) items.push({ kind: 'page', title: sheet.title })
      const ordered = [...(sheet.widgets ?? [])].sort((a, b) => a.layout.y - b.layout.y || a.layout.x - b.layout.x)
      for (const widget of ordered) {
        if (widget.kind === 'text' && 'text' in widget) {
          items.push({ kind: 'text', title: widget.title ?? '', text: widget.text })
          continue
        }
        if (widget.kind !== 'chart' || !('chart_ref' in widget)) continue
        const chart = await client.fetchQuery(chartDocumentQuery(epoch, widget.chart_ref.chart_id, widget.chart_ref.revision, editing))
        const title = widget.title || chart.title
        // Each page with the filters and selections that apply to it.
        const context = chartContext({ definition, chart, widgetId: widget.widget_id, pageId: sheet.page_id, values: appliedValues, crossFilters })
        const request = chartRequest({ chart, draft: editing, context })
        if (!request) continue
        const result = await client.fetchQuery(chartDataQuery(epoch, chart, request))
        if (chart.visual === 'map') {
          // A map prints as drawn on screen, basemap included; off screen it prints its rows.
          const canvas = document.querySelector<HTMLCanvasElement>(`[gs-id="${CSS.escape(widget.widget_id)}"] canvas`)
          if (canvas?.width) {
            const width = Math.round(Math.min(720, canvas.getBoundingClientRect().width || 640))
            items.push({ kind: 'image', title, src: canvas.toDataURL('image/png'), width })
            continue
          }
        } else if (hasImage(chart)) {
          // Same size as on screen, redrawn in the light theme for paper.
          const box = document.querySelector<HTMLElement>(`[gs-id="${CSS.escape(widget.widget_id)}"] [role="img"]`)?.getBoundingClientRect()
          const width = Math.round(Math.min(720, Math.max(360, box?.width ?? 640)))
          const height = Math.round(Math.max(220, Math.min(420, box?.height ?? 320)))
          items.push({ kind: 'image', title, src: await printImage(chart, result, width, height), width })
          continue
        }
        const table = resultTable(result)
        const first = table.rows[0]?.find(value => typeof value === 'number')
        items.push(chart.visual === 'kpi' && typeof first === 'number'
          ? { kind: 'kpi', title, value: new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(first) }
          : { kind: 'table', title, table })
      }
      }
      const made = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date())
      const filters = [
        ...(definition.filters ?? []).filter(item => appliedValues[item.filter_id]).map(item => `${item.title}: ${describeFilterValue(appliedValues[item.filter_id])}`),
        ...crossFilters.map(item => `Selected ${item.label}`),
      ]
      await printHtml(reportHtml({ title: definition.title, subtitle: `${many ? `${definition.pages.length} pages` : page.title} · Data as of ${made}`, items, filters }))
    } catch (error) {
      exportFailed(error)
    } finally {
      setExporting(false)
    }
  }
  const state = !publicationId ? 'Draft' : publishedRevision === revision ? 'Published' : 'Unpublished changes'
  const compact = density === 'panel'

  const sideInspector = bodyWidth >= 960 && !compact
  const saveText = status === 'saving' ? 'Saving…' : status === 'conflict' ? 'Reloaded after someone else\u2019s change'
    : status === 'error' ? 'Last change not saved' : 'All changes saved'
  const label = '@max-3xl/editor:sr-only'
  return <div className='@container/editor flex min-h-0 min-w-0 flex-1 flex-col bg-background'>
    <p role='status' className='sr-only'>{announcement}</p>
    <header className={cn('flex h-14 shrink-0 items-center gap-3 border-b', compact ? 'px-3' : 'pr-4 pl-2')}>
      {!compact && <Button variant='ghost' size='icon' className='size-9 shrink-0' aria-label='Back to dashboards' onClick={onBack}><ArrowLeft /></Button>}
      <div className='flex min-w-0 flex-1 flex-col justify-center'>
        <div className='flex min-w-0 items-center gap-2'>
          {renaming && canEdit ? <Input autoFocus defaultValue={definition.title} maxLength={256} aria-label='Dashboard title'
            className='h-8 max-w-md text-base font-medium'
            onBlur={event => {
              setRenaming(false)
              const title = event.target.value.trim()
              if (title && title !== definition.title) void apply([{ op: 'dashboard.rename', title }])
            }}
            onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); if (event.key === 'Escape') setRenaming(false) }} />
            : <h2 className='truncate text-base font-medium' title={canEdit ? 'Double-click to rename' : undefined}
              onDoubleClick={() => canEdit && setRenaming(true)}>{definition.title}</h2>}
          <Badge variant={state === 'Published' ? 'secondary' : 'outline'} className='shrink-0 font-normal @max-md/editor:hidden'>{state}</Badge>
        </div>
        <p aria-live='polite' className={cn('truncate text-xs text-muted-foreground', (status === 'conflict' || status === 'error') && 'text-destructive')}>
          <span className='@md/editor:hidden'>{state} · </span>{saveText}
        </p>
      </div>
      <div className='flex shrink-0 items-center gap-1.5'>
        <PresenceAvatars others={presence.others} more={presence.more} />
        {onAskNeva && <Button variant='ghost' size='sm' className='h-9 @max-md/editor:hidden' aria-label='Ask Neva'
          onClick={() => onAskNeva(`Help me improve the dashboard \u201c${definition.title}\u201d.`)}>
          <Sparkles /><span className={label}>Ask Neva</span>
        </Button>}
        {canExport && tab === 'canvas' && <DropdownMenu>
          <Tooltip><TooltipTrigger asChild><DropdownMenuTrigger asChild>
            <Button variant='ghost' size='icon' className='size-9' aria-label='Download' disabled={exporting}><Download /></Button>
          </DropdownMenuTrigger></TooltipTrigger><TooltipContent>Download</TooltipContent></Tooltip>
          <DropdownMenuContent align='end'>
            <DropdownMenuItem onSelect={() => void downloadWorkbook()}><FileSpreadsheet />Data of this page (Excel)</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void printDashboard()}><Printer />Print or save as PDF</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => setScheduleOpen(true)}><CalendarClock />Schedule Excel export…</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>}
        <Tooltip><TooltipTrigger asChild><Button variant='ghost' size='icon' className='relative size-9'
          aria-label={openComments ? `Comments, ${openComments} open` : 'Comments'} onClick={() => setCommentsOpen(true)}>
          <MessageSquare />
          {openComments > 0 && <span aria-hidden className='absolute top-1 right-1 flex min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] leading-4 text-primary-foreground tabular-nums'>{openComments > 99 ? '99+' : openComments}</span>}
        </Button></TooltipTrigger><TooltipContent>Comments</TooltipContent></Tooltip>
        {canEdit && <Tooltip><TooltipTrigger asChild><Button variant='ghost' size='icon' className='size-9' aria-label='Version history' onClick={() => setHistoryOpen(true)}><History /></Button></TooltipTrigger><TooltipContent>Version history</TooltipContent></Tooltip>}
        {canManage && <Button variant='outline' size='sm' className='h-9' aria-label='Share' onClick={() => setShareOpen(true)}><Share2 /><span className={label}>Share</span></Button>}
        {canEdit && <Button variant='outline' size='sm' className='h-9' aria-label={editing ? 'Done' : 'Edit'}
          onClick={() => { setEditing(!editing); setSelected(null); setInspector(null) }}>
          {editing ? <><Check /><span className={label}>Done</span></> : <><Pencil /><span className={label}>Edit</span></>}
        </Button>}
        {canPublish && state !== 'Published' && <Button size='sm' className='h-9' aria-label='Publish' disabled={status === 'saving'} onClick={() => setPublishOpen(true)}>
          <Upload /><span className={label}>Publish</span>
        </Button>}
      </div>
    </header>

    <div className='flex min-h-12 shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-1.5'>
      {canEdit && <Tabs value={tab} onValueChange={value => setTab(value as 'canvas' | 'data')}>
        <TabsList className='h-8'><TabsTrigger value='canvas'>Canvas</TabsTrigger><TabsTrigger value='data'>Data</TabsTrigger></TabsList>
      </Tabs>}
      {tab === 'canvas' && <>
        {canEdit && <span className='h-5 w-px bg-border' aria-hidden />}
        <nav aria-label='Dashboard pages' className='flex min-w-0 flex-1 items-center gap-1 overflow-x-auto'>
          {definition.pages.map(item => {
            const current = item.page_id === page.page_id
            return <button key={item.page_id} type='button' aria-current={current ? 'page' : undefined}
              onClick={() => { setPageId(item.page_id); setSelected(null) }}
              className={cn('h-8 shrink-0 border-b-2 px-2 text-sm transition-colors focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
                current ? 'border-primary font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}>
              {item.title}
            </button>
          })}
          {editing && definition.pages.length < 15 && <Tooltip><TooltipTrigger asChild>
            <Button variant='ghost' size='icon' className='size-8' aria-label='Add page' onClick={addPage}><Plus /></Button>
          </TooltipTrigger><TooltipContent>Add page</TooltipContent></Tooltip>}
        </nav>
      </>}
      {tab === 'canvas' && editing && <div role='toolbar' aria-label='Add to dashboard' className='flex flex-wrap items-center gap-1'>
        <Button variant='ghost' size='sm' onClick={() => setInspector({ mode: 'new', visual: 'bar' })}><BarChart3 />Chart</Button>
        <Button variant='ghost' size='sm' onClick={() => setInspector({ mode: 'new', visual: 'kpi' })}><Hash />KPI</Button>
        <Button variant='ghost' size='sm' onClick={() => setInspector({ mode: 'new', visual: 'table' })}><Table2 />Table</Button>
        <Button variant='ghost' size='sm' onClick={() => setTextOpen(true)}><Type />Text</Button>
        <Button variant='ghost' size='sm' onClick={() => setFiltersOpen(true)}><Filter />Filters</Button>
        <span className='mx-1 h-5 w-px bg-border' aria-hidden />
        <Tooltip><TooltipTrigger asChild><Button variant='ghost' size='icon' className='size-8' aria-label='Undo' disabled={!undo.length || status === 'saving'} onClick={undoLast}><Undo2 /></Button></TooltipTrigger><TooltipContent>Undo</TooltipContent></Tooltip>
        <Tooltip><TooltipTrigger asChild><Button variant='ghost' size='icon' className='size-8' aria-label='Redo' disabled={!redo.length || status === 'saving'} onClick={redoLast}><Redo2 /></Button></TooltipTrigger><TooltipContent>Redo</TooltipContent></Tooltip>
      </div>}
    </div>

    {tab === 'data' && canEdit ? <DashboardDataTab dashboardId={dashboardId} workspaceId={workspaceId} revision={revision}
      onCommitted={receipt => { setRevision(receipt.new_revision); void reload(); void datasets.refetch() }} onReload={() => void reload()} />
      : <div ref={bodyRef} className='relative flex min-h-0 flex-1'>
        <div className='flex min-h-0 min-w-0 flex-1 flex-col'>
          <FilterBar definition={definition} values={filterValues} draft={editing}
            onChange={(id, value) => setFilterValues(current => {
              const next = { ...current }
              if (value) next[id] = value; else delete next[id]
              return next
            })}
            crossFilters={crossFilters} onClearCross={index => setCrossFilters(index === null ? [] : crossFilters.filter((_, i) => i !== index))}
            editing={editing} onEdit={() => setFiltersOpen(true)} readOnly={!canInteract && !editing} />
          <DashboardCanvas definition={definition} page={page} draft={editing} editing={editing}
            collaborators={presence.others} onPointer={presence.point}
            selectedId={selected} onSelect={setSelected} onLayout={onLayout}
            onEditChart={(widget, chart) => setInspector({ mode: 'edit', widget, chart })}
            onDuplicate={duplicate} onRemove={widget => void apply(removal(widget))}
            linksFor={linksFor} onOpenLink={openLink}
            onEditLink={canEdit ? (widget, chart) => setLinking({ widget, chart }) : undefined}
            onAskNeva={onAskNeva ? (_widget, chart) => onAskNeva(chart ? `Explain the chart “${chart.title}” on this dashboard and suggest improvements.` : 'Help me with this widget.') : undefined}
            contextFor={contextFor} onSelectValue={onSelectValue} interactive={canInteract}
            selectedValueFor={widget => {
              const labels = crossFilters.filter(item => item.source_widget_id === widget.widget_id).map(item => item.label)
              return labels.length ? labels : null
            }}
            highlighted={nevaChanged} onDownload={canExport ? (chart, context, format, root) => void downloadChart(chart, context, format, root) : undefined} />
        </div>
        {inspector && <div className={cn('flex min-h-0 border-l bg-card',
          // A side column only when the canvas keeps room for a multi-column layout;
          // otherwise the inspector floats over the canvas instead of squeezing it.
          sideInspector ? 'w-90 shrink-0' : 'absolute inset-y-0 right-0 z-20 w-[min(22.5rem,100%)] shadow-xl')}>
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
    {commentsOpen && page && <CommentsPanel open onOpenChange={setCommentsOpen} dashboardId={dashboardId} definition={definition}
      pageId={page.page_id} selectedWidgetId={selected} canComment={canComment} canManage={canManage} />}
    {scheduleOpen && page && <ScheduleDialog open onOpenChange={setScheduleOpen} dashboardId={dashboardId}
      pages={definition.pages} pageId={page.page_id} />}
    {shareOpen && <ShareDialog open onOpenChange={setShareOpen} dashboardId={dashboardId} workspaceId={workspaceId} definition={definition} />}
    {linking && <LinkDialog open onOpenChange={value => { if (!value) setLinking(null) }} dashboardId={dashboardId}
      widgetId={linking.widget.widget_id} chart={linking.chart} saving={savingLink}
      existing={linksFor(linking.widget)[0]} onSave={link => void saveLink(link)} />}
    <HistorySheet open={historyOpen} onOpenChange={setHistoryOpen} dashboardId={dashboardId} epoch={epoch}
      current={revision} published={publicationId ? publishedRevision : undefined} />
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

const WHEN = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' })

function HistorySheet({ open, onOpenChange, dashboardId, epoch, current, published }: {
  open: boolean; onOpenChange: (open: boolean) => void; dashboardId: string; epoch: number; current: number; published?: number
}) {
  const history = useQuery({
    queryKey: ['native-dataviz', 'history', epoch, dashboardId, current],
    queryFn: ({ signal }) => nativeDocuments(epoch).history('dashboards', dashboardId, { limit: 50, signal }),
    enabled: open, retry: false,
  })
  return <Sheet open={open} onOpenChange={onOpenChange}>
    <SheetContent className='flex min-h-0 flex-col gap-0'>
      <SheetHeader className='border-b'><SheetTitle>Version history</SheetTitle>
        <SheetDescription>Every saved change is kept. Viewers see the published version until you publish again.</SheetDescription></SheetHeader>
      <ol className='min-h-0 flex-1 divide-y overflow-y-auto px-4'>
        {history.isPending ? <li role='status' className='py-3 text-sm text-muted-foreground'>Loading…</li>
          : history.isError ? <li role='alert' className='py-3 text-sm text-muted-foreground'>History is unavailable.</li>
            : history.data.map(item => <li key={item.revision} className='flex items-center justify-between gap-3 py-3'>
              <div className='min-w-0'>
                <p className='text-sm'>Version {item.revision}</p>
                <p className='truncate text-xs text-muted-foreground'>
                  {item.author_name} · <time className='tabular-nums' dateTime={item.created_at}>{WHEN.format(new Date(item.created_at))}</time>
                </p>
              </div>
              <span className='flex shrink-0 gap-1.5'>
                {item.revision === published && <Badge variant='secondary' className='font-normal'>Published</Badge>}
                {item.revision === current && <Badge variant='outline' className='font-normal'>Current</Badge>}
              </span>
            </li>)}
      </ol>
    </SheetContent>
  </Sheet>
}
