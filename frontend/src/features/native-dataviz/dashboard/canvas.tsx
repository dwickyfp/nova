import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { GridStack, type GridItemHTMLElement, type GridStackNode } from 'gridstack'
import 'gridstack/dist/gridstack.css'
import { Copy, MoreHorizontal, Pencil, Sparkles, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import type { ChartDefinition, ChartWidget, DashboardDefinition, FilterTree, TextWidget } from '../contracts'
import { useChartDocument } from './hooks'
import { NativeChart, type ValueSelection } from '../charts/native-chart'
import type { Layout, Page, Widget } from './layout'

type CanvasProps = {
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
  highlight?: { widgetId: string; label: string } | null
}

function ChartBody({ widget, props }: { widget: ChartWidget; props: CanvasProps }) {
  const chart = useChartDocument(widget.chart_ref.chart_id, widget.chart_ref.revision, props.draft)
  const definition = chart.data
  const title = widget.title || definition?.title || 'Chart'
  const showTitle = widget.style?.show_title ?? definition?.style?.show_title ?? true
  const effective = definition && widget.style ? {
    ...definition,
    style: { ...definition.style,
      ...(widget.style.show_legend !== null && widget.style.show_legend !== undefined ? { show_legend: widget.style.show_legend } : {}),
      ...(widget.style.show_labels !== null && widget.style.show_labels !== undefined ? { show_labels: widget.style.show_labels } : {}),
      ...(widget.style.palette ? { palette: widget.style.palette } : {}),
    },
  } as ChartDefinition : definition
  return <WidgetFrame widget={widget} title={showTitle ? title : ''} props={props} chart={definition}>
    {chart.isPending ? <div aria-busy='true' className='min-h-0 flex-1 animate-pulse rounded bg-muted/60' />
      : chart.isError || !effective ? <p role='alert' className='m-auto text-sm text-muted-foreground'>This chart is unavailable with your current role.</p>
        : <NativeChart chart={effective} draft={props.draft} context={props.contextFor(widget, effective)}
          selectedLabel={props.selectedValueFor?.(widget) ?? null}
          onSelectValue={props.editing ? undefined : selection => props.onSelectValue(widget, effective, selection)} />}
  </WidgetFrame>
}

function WidgetFrame({ widget, title, props, chart, children }: {
  widget: Widget; title: string; props: CanvasProps; chart?: ChartDefinition; children: React.ReactNode
}) {
  const selected = props.selectedId === widget.widget_id
  const highlighted = props.highlight?.widgetId === widget.widget_id
  const appearance = widget.appearance
  return <div className={cn('grid-stack-item-content flex flex-col overflow-hidden rounded-lg',
    appearance?.surface === 'transparent' ? 'bg-transparent' : 'bg-card',
    appearance?.border !== false && 'border border-surface-border',
    appearance?.padding === 'none' ? 'p-0' : appearance?.padding === 'comfortable' ? 'p-4' : 'p-3',
    props.editing && 'cursor-grab active:cursor-grabbing',
    selected && 'outline-2 outline-offset-2 outline-ring',
    highlighted && 'ring-2 ring-primary/60 transition-shadow')}
  onClick={event => { if (props.editing) { event.stopPropagation(); props.onSelect(widget.widget_id) } }}>
    {(title || props.editing) && <div className='flex shrink-0 items-start justify-between gap-2 pb-1'>
      <h3 className='type-title min-w-0 truncate text-sm font-semibold' title={title}>{title}</h3>
      <div className={cn('flex shrink-0 items-center gap-0.5', !props.editing && 'opacity-0 focus-within:opacity-100 group-hover/widget:opacity-100')}>
        {props.onAskNeva && <Button variant='ghost' size='icon' className='size-7' aria-label={`Ask Neva about ${title || 'this widget'}`}
          onClick={event => { event.stopPropagation(); props.onAskNeva?.(widget, chart) }}><Sparkles className='size-4' /></Button>}
        {props.editing && <DropdownMenu>
          <DropdownMenuTrigger asChild><Button variant='ghost' size='icon' className='size-7' aria-label={`${title || 'Widget'} actions`}
            onClick={event => event.stopPropagation()}><MoreHorizontal className='size-4' /></Button></DropdownMenuTrigger>
          <DropdownMenuContent align='end'>
            {widget.kind === 'chart' && chart && <DropdownMenuItem onSelect={() => props.onEditChart(widget, chart)}><Pencil />Edit chart</DropdownMenuItem>}
            <DropdownMenuItem onSelect={() => props.onDuplicate(widget)}><Copy />Duplicate</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant='destructive' onSelect={() => props.onRemove(widget)}><Trash2 />Remove</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>}
      </div>
    </div>}
    <div className='flex min-h-0 flex-1 flex-col'>{children}</div>
    {highlighted && props.highlight && <span className='pointer-events-none absolute top-1 right-1 rounded-full bg-primary px-2 py-0.5 text-xs text-primary-foreground'>
      {props.highlight.label}
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
      columnOpts: { breakpoints: [{ w: 520, c: 1 }] },
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
      instance.update(element, { ...widget.layout, noMove: widget.locked, noResize: widget.locked })
    }
    instance.batchUpdate(false)
  }, [signature])

  useEffect(() => {
    grid.current?.setStatic(!props.editing || narrow)
  }, [props.editing, narrow])

  return <div className='min-h-0 flex-1 overflow-y-auto' onClick={() => props.editing && props.onSelect(null)}>
    {props.editing && narrow && <p role='status' className='mx-3 mt-3 rounded-md border border-dashed p-2 text-xs text-muted-foreground'>
      Widen the window to move or resize widgets. Your layout is kept for larger screens.
    </p>}
    {!widgets.length && <div className='m-6 flex flex-col items-center gap-2 rounded-lg border border-dashed p-10 text-center'>
      <p className='font-medium'>This page is empty</p>
      <p className='max-w-sm text-sm text-muted-foreground'>{props.editing ? 'Add a chart, KPI or text from the toolbar, or ask Neva to build one for you.' : 'Nothing has been added to this page yet.'}</p>
    </div>}
    <div ref={host} data-mode={props.editing ? 'edit' : 'view'} aria-label={`${props.page.title} canvas`}
      className={cn('grid-stack nova-dataviz-canvas min-h-24', props.editing && !narrow && 'nova-dataviz-edit-grid')}>
      {widgets.map(widget => <div key={widget.widget_id} className='grid-stack-item group/widget' gs-id={widget.widget_id}
        gs-x={widget.layout.x} gs-y={widget.layout.y} gs-w={widget.layout.w} gs-h={widget.layout.h}
        gs-min-w={2} gs-min-h={2} aria-label={widget.title || undefined}>
        <WidgetBody widget={widget} props={props} />
      </div>)}
    </div>
  </div>
}
