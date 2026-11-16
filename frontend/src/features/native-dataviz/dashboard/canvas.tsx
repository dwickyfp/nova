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
import type { Layout, Page, Widget } from './layout'
import { ROW_PX, type CanvasPoint, type Collaborator } from './presence'
import { PresenceCursors } from './presence-ui'

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
  selectedValueFor?: (widget: Widget) => string | null
  /** Widgets Neva just changed; they carry a brief "Neva" marker. */
  highlighted?: string[]
  /** Links from a chart to other dashboards (drill-through). */
  linksFor?: (widget: Widget) => Interaction[]
  onOpenLink?: (link: Interaction, selection: ValueSelection) => void
  /** Edit mode: add or change a chart's link to another dashboard. */
  onEditLink?: (widget: Widget, chart: ChartDefinition) => void
  /** Present only with Export access: download one chart as a file. */
  onDownload?: (chart: ChartDefinition, context: FilterTree | undefined, format: ChartFileFormat, root: HTMLElement) => void
}

function ChartBody({ widget, props }: { widget: ChartWidget; props: CanvasProps }) {
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
    if (!options.length && !active.length && !links.length) props.onSelectValue(widget, effective, selection)
    else setPending(selection)
  }
  const openable = pending ? links.filter(link => link.mappings?.some(mapping => mapping.source.field_id === pending.field_id)) : []
  const title = active.length ? [baseTitle, ...active.map(step => step.label)].join(' › ') : baseTitle
  return <WidgetFrame widget={widget} title={showTitle || active.length ? title : ''} props={props} chart={definition}
    download={download} canImage={shown ? hasImage(shown) : false}
    drill={active.length ? { back: () => setSteps(active.slice(0, -1)), reset: () => setSteps([]), depth: active.length } : undefined}>
    {chart.isPending ? <div aria-busy='true' className='min-h-0 flex-1 animate-pulse rounded bg-muted/60' />
      : chart.isError || !shown ? <p role='alert' className='m-auto text-sm text-muted-foreground'>This chart is unavailable with your current role.</p>
        : <div className='relative flex min-h-0 flex-1 flex-col'>
          <NativeChart chart={shown} draft={props.draft} context={context}
            selectedLabel={active.length ? null : props.selectedValueFor?.(widget) ?? null}
            onSelectValue={props.editing ? undefined : select} />
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
    highlighted && 'ring-2 ring-primary/60 transition-shadow')}
  onClick={event => { if (props.editing) { event.stopPropagation(); props.onSelect(widget.widget_id) } }}>
    {(title || props.editing) && <div className='flex h-7 shrink-0 items-center justify-between gap-2'>
      <div className='flex min-w-0 items-center gap-1'>
        {drill && <Button variant='ghost' size='icon' className='-ml-1 size-7 shrink-0' aria-label='Back up one level'
          onClick={event => { event.stopPropagation(); drill.back() }}><ArrowLeft className='size-4' /></Button>}
        <h3 className='min-w-0 truncate text-sm font-medium' title={title}>{title}</h3>
        {drill && drill.depth > 1 && <Button variant='link' size='sm' className='h-7 shrink-0 px-1 text-xs text-muted-foreground'
          onClick={event => { event.stopPropagation(); drill.reset() }}>Reset</Button>}
      </div>
      <div className={cn('flex shrink-0 items-center gap-0.5', !props.editing && 'opacity-0 focus-within:opacity-100 group-hover/widget:opacity-100')}>
        {props.onAskNeva && <Button variant='ghost' size='icon' className='size-7' aria-label={`Ask Neva about ${title || 'this widget'}`}
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
    {highlighted && <span role='status' className='pointer-events-none absolute top-1 right-1 flex items-center gap-1 rounded-full bg-primary px-2 py-0.5 text-xs text-primary-foreground motion-safe:animate-in motion-safe:fade-in'>
      <Sparkles className='size-3' aria-hidden />Changed by Neva
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

/**
 * The 12-column canvas. React owns each widget element; GridStack only measures
 * and moves them. The saved x/y/w/h is authoritative, and a layout is committed
 * once per completed drag or resize, never per pixel.
 */
export function DashboardCanvas(props: CanvasProps) {
  const host = useRef<HTMLDivElement | null>(null)
  const grid = useRef<GridStack | null>(null)
  const commit = useRef(props.onLayout)
  const [narrow, setNarrow] = useState(false)
  useEffect(() => { commit.current = props.onLayout }, [props.onLayout])
  const widgets = props.page.widgets ?? []
  const signature = widgets.map(item => `${item.widget_id}:${item.layout.x},${item.layout.y},${item.layout.w},${item.layout.h}:${item.locked}`).join('|')

  useLayoutEffect(() => {
    const node = host.current
    if (!node) return
    const instance = GridStack.init({
      column: 12, cellHeight: 72, margin: 6, animate: true,
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
    const observer = new ResizeObserver(() => setNarrow(node.clientWidth < 520))
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
      instance.update(element, instance.getColumn() === 1
        ? { noMove: widget.locked, noResize: widget.locked }
        : { ...widget.layout, noMove: widget.locked, noResize: widget.locked })
    }
    instance.batchUpdate(false)
  }, [signature])

  // The canvas, not GridStack, decides the column count, so a narrow view and
  // the saved 12-column layout never drift apart.
  const layouts = useRef(widgets)
  layouts.current = widgets
  useLayoutEffect(() => {
    const instance = grid.current
    const node = host.current
    if (!instance || !node) return
    instance.batchUpdate()
    if (narrow) {
      instance.column(1, 'list')
    } else {
      instance.column(12, 'none')
      // Back from one column: the saved layout is authoritative, not a re-flow.
      for (const element of node.querySelectorAll<HTMLElement>(':scope > .grid-stack-item')) {
        const widget = layouts.current.find(item => item.widget_id === element.getAttribute('gs-id'))
        if (widget) instance.update(element, { ...widget.layout })
      }
    }
    instance.batchUpdate(false)
  }, [narrow])

  useEffect(() => {
    grid.current?.setStatic(!props.editing || narrow)
  }, [props.editing, narrow])

  return <div className='min-h-0 flex-1 overflow-y-auto p-2.5' onClick={() => props.editing && props.onSelect(null)}>
    {props.editing && narrow && <p role='status' className='m-1.5 rounded-md border border-dashed p-2 text-xs text-muted-foreground'>
      Widen the window to move or resize widgets. Your layout is kept for larger screens.
    </p>}
    {!widgets.length && <div className='m-1.5 flex flex-col items-center gap-2 rounded-lg border border-dashed px-6 py-12 text-center'>
      <p className='text-sm font-medium'>This page is empty</p>
      <p className='max-w-sm text-sm text-muted-foreground'>{props.editing ? 'Add a chart, KPI or text from the toolbar, or ask Neva to build one for you.' : 'Nothing has been added to this page yet.'}</p>
    </div>}
    <div className='relative'
      onPointerMove={props.onPointer ? event => {
        const box = host.current?.getBoundingClientRect()
        if (box?.width) props.onPointer?.({ x: (event.clientX - box.left) / box.width, y: (event.clientY - box.top) / ROW_PX })
      } : undefined}
      onPointerLeave={props.onPointer ? () => props.onPointer?.(null) : undefined}>
    <div ref={host} data-mode={props.editing ? 'edit' : 'view'} aria-label={`${props.page.title} canvas`}
      className={cn('grid-stack nova-dataviz-canvas min-h-24', props.editing && !narrow && 'nova-dataviz-edit-grid')}>
      {widgets.map(widget => <div key={widget.widget_id} className='grid-stack-item group/widget' gs-id={widget.widget_id}
        gs-x={widget.layout.x} gs-y={widget.layout.y} gs-w={widget.layout.w} gs-h={widget.layout.h}
        gs-min-w={2} gs-min-h={2} aria-label={widget.title || undefined}>
        <WidgetBody widget={widget} props={props} />
      </div>)}
    </div>
    {props.collaborators?.length ? <PresenceCursors others={props.collaborators} pageId={props.page.page_id} /> : null}
    </div>
  </div>
}
