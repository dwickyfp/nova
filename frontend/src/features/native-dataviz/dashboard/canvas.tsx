import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { GridStack, type GridItemHTMLElement, type GridStackNode } from 'gridstack'
import 'gridstack/dist/gridstack.css'
import { ArrowLeft, ChevronsDown, Copy, Download, ExternalLink, FileSpreadsheet, FileText, Filter, ImageIcon, Link2, MoreHorizontal, Pencil, Sparkles, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuSub,
  DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import type { ChartDefinition, ChartWidget, DashboardDefinition, FilterTree, Interaction, TextWidget } from '../contracts'
import { hasImage, type ChartFileFormat } from './export'
import { useChartDocument } from './hooks'
import { useDatasetDefinition } from '../charts/chart-data'
import { drillContext, drillDimension, drilledChart, drillOptions, type DrillStep } from '../charts/drill'
import { NativeChart, type ValueSelection } from '../charts/native-chart'
import { activeDrop, DROP_TYPE, dropSize, slotAt, type DropItem, type Layout, type Page, type Widget } from './layout'
import { ROW_PX, type CanvasPoint, type Collaborator } from './presence'
import { PresenceCursors } from './presence-ui'
import { NevaPointer, type NevaActivity } from './neva-pointer'

type CanvasProps = {
  /** Other people on this dashboard, and where this viewer's pointer is. */
  collaborators?: Collaborator[]
  onPointer?: (point: CanvasPoint | null) => void
  definition: DashboardDefinition
  page: Page
  draft: boolean
  editing: boolean
  selectedId: string | null
  onSelect: (widgetId: string | null) => void
  onLayout: (placements: { widget_id: string; layout: Layout }[]) => void
  onEditChart: (widget: Widget, chart: ChartDefinition) => void
  onDuplicate: (widget: Widget) => void
  onRemove: (widget: Widget) => void
  onAskNeva?: (widget: Widget, chart: ChartDefinition | undefined) => void
  contextFor: (widget: Widget, chart: ChartDefinition) => FilterTree | undefined
  onSelectValue: (widget: Widget, chart: ChartDefinition, selection: ValueSelection) => void
  /** Without Interact access, charts do not filter, drill or open links on click. */
  interactive?: boolean
  selectedValueFor?: (widget: Widget) => string[] | null
  /** Widgets the assistant just changed; they carry a brief marker. */
  highlighted?: string[]
  /** A change the assistant is making on this page: its pointer sits on the target. */
  neva?: { activity: NevaActivity; short: string } | null
  /** Links from a chart to other dashboards (drill-through). */
  linksFor?: (widget: Widget) => Interaction[]
  onOpenLink?: (link: Interaction, selection: ValueSelection) => void
  /** Edit mode: add or change a chart's link to another dashboard. */
  onEditLink?: (widget: Widget, chart: ChartDefinition) => void
  /** Edit mode: a toolbar item was dropped on a canvas cell. */
  onDropWidget?: (item: DropItem, cell: { x: number; y: number }) => void
  /** Present only with Export access: download one chart as a file. */
  onDownload?: (chart: ChartDefinition, context: FilterTree | undefined, format: ChartFileFormat, root: HTMLElement) => void
}

/** How far outside the screen a chart starts loading, so it is ready when scrolled to. */
const NEAR_SCREEN = '600px 0px'

/**
 * True once the element has come near the screen, and from then on. A page can
 * hold up to 100 widgets; only those near the screen query and draw, so a long
 * page does not open every chart at once.
 */
function useNearScreen<T extends Element>(): [(node: T | null) => void, boolean] {
  const [node, setNode] = useState<T | null>(null)
  const [near, setNear] = useState(() => typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    if (near || !node) return
    // The canvas scrolls inside its own box; measure against it so the margin counts there.
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) setNear(true)
    }, { root: node.closest('[data-canvas-scroll]'), rootMargin: NEAR_SCREEN })
    observer.observe(node)
    return () => observer.disconnect()
  }, [near, node])
  return [setNode, near]
}

function ChartBody({ widget, props }: { widget: ChartWidget; props: CanvasProps }) {
  const [place, near] = useNearScreen<HTMLDivElement>()
  const chart = useChartDocument(widget.chart_ref.chart_id, widget.chart_ref.revision, props.draft)
  const definition = chart.data
  const dataset = useDatasetDefinition(definition?.dataset_id, definition?.dataset_revision)
  const [steps, setSteps] = useState<DrillStep[]>([])
  const [pending, setPending] = useState<ValueSelection | null>(null)
  const baseTitle = widget.title || definition?.title || 'Chart'
  const showTitle = widget.style?.show_title ?? definition?.style?.show_title ?? true
  const effective = definition && widget.style ? {
    ...definition,
    style: { ...definition.style,
      ...(widget.style.show_legend !== null && widget.style.show_legend !== undefined ? { show_legend: widget.style.show_legend } : {}),
      ...(widget.style.show_labels !== null && widget.style.show_labels !== undefined ? { show_labels: widget.style.show_labels } : {}),
      ...(widget.style.palette ? { palette: widget.style.palette } : {}),
    },
  } as ChartDefinition : definition
  // Editing shows the chart as saved; drilling is a way of reading it.
  const active = props.editing ? [] : steps
  const shown = effective ? drilledChart(effective, active) : undefined
  const context = effective ? drillContext(effective, active, props.contextFor(widget, effective)) : undefined
  const options = shown && !props.editing && dataset.data
    ? drillOptions(shown, dataset.data.fields, [drillDimension(effective!)?.field_id ?? '', ...active.map(step => step.field_id)])
    : []
  const download = shown && props.onDownload
    ? (format: ChartFileFormat, root: HTMLElement) => props.onDownload?.(shown, context, format, root)
    : undefined
  const links = props.editing ? [] : props.linksFor?.(widget) ?? []
  const select = (selection: ValueSelection) => {
    if (!effective || !shown) return
    // Without anything to drill into or open, a click filters the dashboard as before.
    // Adding to a selection or picking a legend entry filters directly, with no menu.
    if (selection.additive || selection.fromLegend || (!options.length && !active.length && !links.length)) {
      props.onSelectValue(widget, effective, selection)
    }
    else setPending(selection)
  }
  const openable = pending ? links.filter(link => link.mappings?.some(mapping => mapping.source.field_id === pending.field_id)) : []
  const title = active.length ? [baseTitle, ...active.map(step => step.label)].join(' › ') : baseTitle
  return <WidgetFrame widget={widget} title={showTitle || active.length ? title : ''} props={props} chart={definition}
    download={download} canImage={shown ? hasImage(shown) : false}
    drill={active.length ? { back: () => setSteps(active.slice(0, -1)), reset: () => setSteps([]), depth: active.length } : undefined}>
    {chart.isPending ? <div aria-busy='true' className='min-h-0 flex-1 motion-safe:animate-pulse rounded bg-muted/60' />
      : chart.isError || !shown ? <p role='alert' className='m-auto text-sm text-muted-foreground'>This chart is unavailable with your current role.</p>
        : <div ref={place} className='relative flex min-h-0 flex-1 flex-col'>
          {near ? <NativeChart chart={shown} draft={props.draft} context={context}
            selectedLabel={active.length ? null : props.selectedValueFor?.(widget) ?? null}
            onSelectValue={props.editing || props.interactive === false ? undefined : select} />
            : <div data-waiting-chart='' className='min-h-0 flex-1 rounded bg-muted/40' />}
          {pending && <DropdownMenu open onOpenChange={open => { if (!open) setPending(null) }}>
            <DropdownMenuTrigger asChild>
              <span aria-hidden className='pointer-events-none absolute size-px'
                style={{ left: pending.anchor?.x ?? 16, top: pending.anchor?.y ?? 16 }} />
            </DropdownMenuTrigger>
            <DropdownMenuContent align='start' className='w-64'>
              <DropdownMenuLabel className='truncate text-xs font-normal text-muted-foreground'>{pending.label}</DropdownMenuLabel>
              {!active.length && effective && <DropdownMenuItem onSelect={() => props.onSelectValue(widget, effective, pending)}>
                <Filter />Filter the dashboard to this
              </DropdownMenuItem>}
              {openable.map(link => <DropdownMenuItem key={link.action_id} onSelect={() => props.onOpenLink?.(link, pending)}>
                <ExternalLink /><span className='truncate'>{link.label ?? 'Open the linked dashboard'}</span>
              </DropdownMenuItem>)}
              {options.length > 0 && <DropdownMenuSub>
                <DropdownMenuSubTrigger><ChevronsDown />Drill into this by</DropdownMenuSubTrigger>
                <DropdownMenuSubContent className='max-h-72 overflow-y-auto'>
                  {options.map(option => <DropdownMenuItem key={`${option.field_id}:${option.date_grain ?? ''}`}
                    onSelect={() => setSteps([...active, { field_id: pending.field_id, value: pending.value, label: pending.label,
                      period: pending.period, by: { field_id: option.field_id, date_grain: option.date_grain } }])}>
                    {option.label}
                  </DropdownMenuItem>)}
                </DropdownMenuSubContent>
              </DropdownMenuSub>}
            </DropdownMenuContent>
          </DropdownMenu>}
        </div>}
  </WidgetFrame>
}

function WidgetFrame({ widget, title, props, chart, download, canImage = false, drill, children }: {
  widget: Widget; title: string; props: CanvasProps; chart?: ChartDefinition
  download?: (format: ChartFileFormat, root: HTMLElement) => void; canImage?: boolean
  drill?: { back: () => void; reset: () => void; depth: number }; children: React.ReactNode
}) {
  const root = useRef<HTMLDivElement>(null)
  const selected = props.selectedId === widget.widget_id
  const highlighted = props.highlighted?.includes(widget.widget_id) ?? false
  const appearance = widget.appearance
  return <div ref={root} className={cn('grid-stack-item-content flex flex-col overflow-hidden rounded-lg',
    appearance?.surface === 'transparent' ? 'bg-transparent' : 'bg-card',
    appearance?.border !== false && 'border border-surface-border',
    appearance?.padding === 'none' ? 'p-0' : appearance?.padding === 'comfortable' ? 'p-4' : 'p-3',
    props.editing && 'cursor-grab active:cursor-grabbing',
    selected && 'outline-2 outline-offset-2 outline-ring',
    highlighted && 'ring-2 ring-brand/60 motion-safe:transition-shadow')}
  onClick={event => { if (props.editing) { event.stopPropagation(); props.onSelect(widget.widget_id) } }}
  // In Edit, Tab reaches every widget and focusing one selects it, so Alt+arrows can move it.
  tabIndex={props.editing ? 0 : undefined}
  aria-label={props.editing ? `${title || (widget.kind === 'text' ? 'Text' : 'Widget')}, press Alt and an arrow key to move it` : undefined}
  onFocus={event => { if (props.editing && event.target === event.currentTarget) props.onSelect(widget.widget_id) }}>
    {(title || props.editing) && <div className='flex h-7 shrink-0 items-center justify-between gap-2'>
      <div className='flex min-w-0 items-center gap-1'>
        {drill && <Button variant='ghost' size='icon' className='-ml-1 size-7 shrink-0' aria-label='Back up one level'
          onClick={event => { event.stopPropagation(); drill.back() }}><ArrowLeft className='size-4' /></Button>}
        <h3 className='min-w-0 truncate text-sm font-medium' title={title}>{title}</h3>
        {drill && drill.depth > 1 && <Button variant='link' size='sm' className='h-7 shrink-0 px-1 text-xs text-muted-foreground'
          onClick={event => { event.stopPropagation(); drill.reset() }}>Reset</Button>}
      </div>
      <div className={cn('flex shrink-0 items-center gap-0.5', !props.editing && 'opacity-0 focus-within:opacity-100 group-hover/widget:opacity-100')}>
        {props.onAskNeva && <Button variant='ghost' size='icon' className='size-7' aria-label={`Ask the assistant about ${title || 'this widget'}`}
          onClick={event => { event.stopPropagation(); props.onAskNeva?.(widget, chart) }}><Sparkles className='size-4' /></Button>}
        {download && <DropdownMenu>
          <DropdownMenuTrigger asChild><Button variant='ghost' size='icon' className='size-7' aria-label={`Download ${title || 'chart'}`}
            onClick={event => event.stopPropagation()}><Download className='size-4' /></Button></DropdownMenuTrigger>
          <DropdownMenuContent align='end'>
            <DropdownMenuLabel className='text-xs font-normal text-muted-foreground'>Download</DropdownMenuLabel>
            {canImage && <DropdownMenuItem onSelect={() => root.current && download('png', root.current)}><ImageIcon />Image (PNG)</DropdownMenuItem>}
            <DropdownMenuItem onSelect={() => root.current && download('csv', root.current)}><FileText />Data (CSV)</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => root.current && download('xlsx', root.current)}><FileSpreadsheet />Data (Excel)</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>}
        {props.editing && <DropdownMenu>
          <DropdownMenuTrigger asChild><Button variant='ghost' size='icon' className='size-7' aria-label={`${title || 'Widget'} actions`}
            onClick={event => event.stopPropagation()}><MoreHorizontal className='size-4' /></Button></DropdownMenuTrigger>
          <DropdownMenuContent align='end'>
            {widget.kind === 'chart' && chart && <DropdownMenuItem onSelect={() => props.onEditChart(widget, chart)}><Pencil />Edit chart</DropdownMenuItem>}
            {widget.kind === 'chart' && chart && props.onEditLink && <DropdownMenuItem onSelect={() => props.onEditLink?.(widget, chart)}><Link2 />Link to a dashboard…</DropdownMenuItem>}
            <DropdownMenuItem onSelect={() => props.onDuplicate(widget)}><Copy />Duplicate</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant='destructive' onSelect={() => props.onRemove(widget)}><Trash2 />Remove</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>}
      </div>
    </div>}
    <div className='flex min-h-0 flex-1 flex-col'>{children}</div>
    {highlighted && <span role='status' className='pointer-events-none absolute top-1 right-1 flex items-center gap-1 rounded-full bg-brand px-2 py-0.5 text-xs text-brand-foreground motion-safe:animate-in motion-safe:fade-in'>
      <Sparkles className='size-3' aria-hidden />Changed by the assistant
    </span>}
  </div>
}

function TextBody({ widget, props }: { widget: TextWidget; props: CanvasProps }) {
  return <WidgetFrame widget={widget} title={widget.title ?? ''} props={props}>
    <p className={cn('min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words',
      widget.style === 'title' ? 'type-display text-2xl font-semibold' : 'text-sm text-muted-foreground')}>{widget.text}</p>
  </WidgetFrame>
}

function WidgetBody({ widget, props }: { widget: Widget; props: CanvasProps }) {
  if (widget.kind === 'chart' && 'chart_ref' in widget) return <ChartBody widget={widget} props={props} />
  if (widget.kind === 'text' && 'text' in widget) return <TextBody widget={widget} props={props} />
  return <WidgetFrame widget={widget} title={widget.title ?? ''} props={props}><span /></WidgetFrame>
}

/** Reduced motion: widgets jump to their place instead of sliding there. */
function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Empty rows kept below the lowest widget while editing, to drag or resize into. */
const ROOM_ROWS = 3

function columnsFor(width: number): 1 | 6 | 12 {
  return width < 520 ? 1 : width < 900 ? 6 : 12
}

/**
 * Show the saved 12-column layout at the current width. The saved layout is
 * always restored first, so a narrower view is derived from it and never drifts.
 */
function applyColumns(instance: GridStack, node: HTMLElement, widgets: Widget[], columns: 1 | 6 | 12) {
  if (instance.getColumn() !== 12) instance.column(12, 'none')
  for (const element of node.querySelectorAll<HTMLElement>(':scope > .grid-stack-item')) {
    const widget = widgets.find(item => item.widget_id === element.getAttribute('gs-id'))
    if (widget) instance.update(element, { ...widget.layout })
  }
  if (columns === 6) instance.column(6, 'moveScale')
  if (columns === 1) instance.column(1, 'list')
}

/**
 * The 12-column canvas. React owns each widget element; GridStack only measures
 * and moves them. The saved x/y/w/h is authoritative, and a layout is committed
 * once per completed drag or resize, never per pixel.
 */
export function DashboardCanvas(props: CanvasProps) {
  const host = useRef<HTMLDivElement | null>(null)
  const grid = useRef<GridStack | null>(null)
  const commit = useRef(props.onLayout)
  // Phone: one column; tablet: six columns scaled from the saved twelve.
  const [columns, setColumns] = useState<1 | 6 | 12>(12)
  const narrow = columns !== 12
  useEffect(() => { commit.current = props.onLayout }, [props.onLayout])
  const widgets = props.page.widgets ?? []
  const layoutsRef = useRef(widgets)
  layoutsRef.current = widgets
  const signature = widgets.map(item => `${item.widget_id}:${item.layout.x},${item.layout.y},${item.layout.w},${item.layout.h}:${item.locked}`).join('|')

  useLayoutEffect(() => {
    const node = host.current
    if (!node) return
    const instance = GridStack.init({
      // The saved x/y is authoritative: never pull widgets up into empty rows.
      column: 12, cellHeight: 72, margin: 6, animate: !prefersReducedMotion(), mode: 'float',
      staticGrid: true, disableResize: false, resizable: { handles: 'se,e,s' },
    }, node)
    if (!instance) return
    grid.current = instance
    const save = () => {
      const nodes = (instance.engine.nodes as GridStackNode[]).filter(item => item.id)
      commit.current(nodes.map(item => ({ widget_id: String(item.id),
        layout: { x: item.x ?? 0, y: item.y ?? 0, w: item.w ?? 2, h: item.h ?? 2 } })))
    }
    instance.on('dragstop', save)
    instance.on('resizestop', save)
    const observer = new ResizeObserver(() => setColumns(columnsFor(node.clientWidth)))
    observer.observe(node)
    return () => {
      observer.disconnect()
      instance.offAll()
      instance.destroy(false)
      grid.current = null
    }
  }, [])

  useLayoutEffect(() => {
    const instance = grid.current
    const node = host.current
    if (!instance || !node) return
    instance.batchUpdate()
    const elements = [...node.querySelectorAll<HTMLElement>(':scope > .grid-stack-item')]
    const ids = new Set(elements.map(element => element.getAttribute('gs-id')))
    for (const existing of [...instance.engine.nodes]) {
      if (!existing.el || !ids.has(String(existing.id)) || !node.contains(existing.el)) instance.removeWidget(existing.el!, false)
    }
    for (const element of elements) {
      const widget = widgets.find(item => item.widget_id === element.getAttribute('gs-id'))
      if (!widget) continue
      if (!(element as GridItemHTMLElement).gridstackNode) instance.makeWidget(element)
      instance.update(element, { noMove: widget.locked, noResize: widget.locked })
    }
    applyColumns(instance, node, layoutsRef.current, columnsRef.current)
    instance.batchUpdate(false)
  }, [signature])

  // The canvas, not GridStack, decides the column count, so a narrow view and
  // the saved 12-column layout never drift apart.
  const columnsRef = useRef(columns)
  columnsRef.current = columns
  useLayoutEffect(() => {
    const instance = grid.current
    const node = host.current
    if (!instance || !node) return
    instance.batchUpdate()
    applyColumns(instance, node, layoutsRef.current, columns)
    instance.batchUpdate(false)
  }, [columns])

  useEffect(() => {
    grid.current?.setStatic(!props.editing || narrow)
  }, [props.editing, narrow])

  // While editing, the canvas shows every cell of its 12-column grid, filling
  // the view and a few rows below the lowest widget, so its width and height
  // are visible. GridStack keeps that many rows open to drop into.
  const scroller = useRef<HTMLDivElement | null>(null)
  const [rows, setRows] = useState(0)
  const tiles = props.editing && !narrow
  useLayoutEffect(() => {
    const instance = grid.current
    if (!instance) return
    if (!tiles) {
      instance.updateOptions({ minRow: 0 })
      setRows(0)
      return
    }
    const fit = () => {
      const used = Math.max(0, ...(instance.engine.nodes as GridStackNode[]).map(item => (item.y ?? 0) + (item.h ?? 0)))
      const view = Math.floor((scroller.current?.clientHeight ?? 0) / ROW_PX)
      const target = Math.max(used + ROOM_ROWS, view)
      instance.updateOptions({ minRow: target })
      setRows(target)
    }
    fit()
    instance.on('change', fit)
    const observer = new ResizeObserver(fit)
    if (scroller.current) observer.observe(scroller.current)
    return () => {
      instance.off('change')
      observer.disconnect()
    }
  }, [tiles, signature])

  // A toolbar item dragged over the canvas shows where it will land.
  const [drop, setDrop] = useState<Layout | null>(null)
  const dropping = tiles && Boolean(props.onDropWidget)
  const cellAt = (event: { clientX: number; clientY: number }) => {
    const box = host.current?.getBoundingClientRect()
    if (!box?.width) return null
    return { x: Math.floor((event.clientX - box.left) / (box.width / 12)), y: Math.max(0, Math.floor((event.clientY - box.top) / ROW_PX)) }
  }
  const accepts = (event: React.DragEvent) => dropping && event.dataTransfer.types.includes(DROP_TYPE)
  const onDragOver = (event: React.DragEvent) => {
    if (!accepts(event)) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
    const cell = cellAt(event)
    const item = activeDrop.item
    if (!cell || !item) return
    const size = dropSize(item)
    const next = slotAt(widgets, size.w, size.h, cell)
    setDrop(current => current && current.x === next.x && current.y === next.y && current.w === next.w && current.h === next.h ? current : next)
  }
  const onDrop = (event: React.DragEvent) => {
    if (!accepts(event)) return
    event.preventDefault()
    setDrop(null)
    const cell = cellAt(event)
    let item: DropItem | null
    try { item = JSON.parse(event.dataTransfer.getData(DROP_TYPE)) as DropItem } catch { item = null }
    if (cell && item && (item.kind === 'text' || (item.kind === 'chart' && typeof item.visual === 'string'))) props.onDropWidget?.(item, cell)
  }

  return <div ref={scroller} data-canvas-scroll='' className='min-h-0 flex-1 overflow-y-auto p-2.5' onClick={() => props.editing && props.onSelect(null)}>
    {props.editing && narrow && <p role='status' className='m-1.5 rounded-md border border-dashed p-2 text-xs text-muted-foreground'>
      {columns === 1 ? 'Widen the window to move or resize widgets.' : 'Widen the window to move or resize widgets; this narrower view is scaled from the full layout.'} Your layout is kept for larger screens.
    </p>}
    {!widgets.length && <div className='m-1.5 flex flex-col items-center gap-2 rounded-lg border border-dashed px-6 py-12 text-center'>
      <p className='text-sm font-medium'>This page is empty</p>
      <p className='max-w-sm text-sm text-muted-foreground'>{props.editing ? 'Add a chart, KPI or text from the toolbar, or ask the assistant to build one for you.' : 'Nothing has been added to this page yet.'}</p>
    </div>}
    <div className='relative'
      onDragOver={dropping ? onDragOver : undefined} onDrop={dropping ? onDrop : undefined}
      onDragLeave={dropping ? event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDrop(null) } : undefined}
      onPointerMove={props.onPointer ? event => {
        const box = host.current?.getBoundingClientRect()
        if (box?.width) props.onPointer?.({ x: (event.clientX - box.left) / box.width, y: (event.clientY - box.top) / ROW_PX })
      } : undefined}
      onPointerLeave={props.onPointer ? () => props.onPointer?.(null) : undefined}>
    {tiles && rows > 0 && <div aria-hidden data-testid='canvas-cells' className='nova-dataviz-cells'
      style={{ gridTemplateRows: `repeat(${rows}, ${ROW_PX}px)` }}>
      {Array.from({ length: rows * 12 }, (_, index) => <span key={index} />)}
    </div>}
    <div ref={host} data-mode={props.editing ? 'edit' : 'view'} aria-label={`${props.page.title} canvas`}
      className='grid-stack nova-dataviz-canvas min-h-24'>
      {widgets.map(widget => <div key={widget.widget_id} className='grid-stack-item group/widget' gs-id={widget.widget_id}
        gs-x={widget.layout.x} gs-y={widget.layout.y} gs-w={widget.layout.w} gs-h={widget.layout.h}
        gs-min-w={2} gs-min-h={2} aria-label={widget.title || undefined}
        data-neva-target={props.neva?.activity.widget_ids.includes(widget.widget_id) ? props.neva.activity.state : undefined}>
        <WidgetBody widget={widget} props={props} />
      </div>)}
    </div>
    {drop && <div aria-hidden data-testid='drop-preview' className='nova-dataviz-drop'
      style={{ left: `${drop.x / 12 * 100}%`, width: `${drop.w / 12 * 100}%`, top: drop.y * ROW_PX, height: drop.h * ROW_PX }} />}
    {props.collaborators?.length ? <PresenceCursors others={props.collaborators} pageId={props.page.page_id} /> : null}
    {props.neva && (!props.neva.activity.page_id || props.neva.activity.page_id === props.page.page_id)
      ? <NevaPointer activity={props.neva.activity} short={props.neva.short} host={host} /> : null}
    </div>
  </div>
}
