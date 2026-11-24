import { createContext, memo, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { flushSync } from 'react-dom'
import { ArrowLeft, ChevronsDown, Copy, Download, ExternalLink, FileSpreadsheet, FileText, Filter, ImageIcon, Link2, Lock, MoreHorizontal, Pencil, Sparkles, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuSub,
  DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import type {
  ChartDefinition, ChartWidget, ConnectorElement, DashboardDefinition, ElementPlacement, FilterTree, Geometry,
  Interaction, LegacySpacer, RichText, ShapeElement, TextElement,
} from '../contracts'
import { hasImage, type ChartFileFormat } from './export'
import { useChartDocument } from './hooks'
import { useDatasetDefinition } from '../charts/chart-data'
import { drillContext, drillDimension, drilledChart, drillOptions, type DrillStep } from '../charts/drill'
import { NativeChart, type ValueSelection } from '../charts/native-chart'
import {
  activeDrop, cellRect, DROP_TYPE, dropSize, gridWidgets, pageGrid, slotAt, widgetOf, type DropItem, type Element as PageElement,
  type Layout, type Page, type Widget,
} from './layout'
import {
  absoluteBoxes, boxStyle, clampBox, columnEdges, connectorPath, dashArray, fillCss, frameGrid, HANDLES, mobileHeight,
  mobileOrder, opacityOf, PHONE_WIDTH, themeRadius, pageSize, readingOrder, resizeBox, sameBox, scaleFor, SNAP_DISTANCE, strokeColor, tabOrder,
  toGeometry, dropShadowOf, type Box, type Edges } from './frame'
import { bounds, index, otherBoxes, smartSnap, type Guide } from './authoring'
import { ICONS } from './icons'
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
  /** Edit mode: the selected items, by element ID. */
  selection: readonly string[]
  onSelect: (elementIds: string[]) => void
  /** Edit mode: one completed move or resize, in page pixels. */
  onArrange: (placements: ElementPlacement[]) => void
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
  /** Edit mode: a toolbar item was dropped on the page, at a 12-column cell. */
  onDropWidget?: (item: DropItem, cell: { x: number; y: number }) => void
  /** Present only with Export access: download one chart as a file. */
  onDownload?: (chart: ChartDefinition, context: FilterTree | undefined, format: ChartFileFormat, root: HTMLElement) => void
}

/** A spacer kept from a grid dashboard: an empty frame that holds space, drawn above the page's items. */
type SpacerItem = LegacySpacer & { kind: 'spacer'; hidden?: false; decorative: true; name?: undefined }
/** Anything placed on the page. */
type Placed = PageElement | SpacerItem

/**
 * How the page is drawn right now: its scale, whether it is the phone column,
 * the saved page, the selection and the group being edited. None of this
 * changes while an item is dragged, so a drag redraws only what moves.
 */
type Frame = {
  scale: number; phone: boolean; page: Page; widgets: Map<string, Widget>
  selection: ReadonlySet<string>; scope: string | null; select: (id: string, additive: boolean) => void
}

/** How far outside the screen a chart starts loading, so it is ready when scrolled to. */
const NEAR_SCREEN = '600px 0px'
/** Page pixels kept free below a scrolling page while editing, to drag into. */
const EDIT_ROOM = 216
/** A press becomes a drag after the pointer moves this many screen pixels. */
const DRAG_START = 3

/**
 * True once the element has come near the screen, and from then on. A page can
 * hold up to 150 items; only charts near the screen query and draw, so a long
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

function ChartBody({ element, widget, props }: { element: PageElement; widget: ChartWidget; props: CanvasProps }) {
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
  return <WidgetFrame element={element} widget={widget} title={showTitle || active.length ? title : ''} props={props} chart={definition}
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

function WidgetFrame({ element, widget, title, props, chart, download, canImage = false, drill, children }: {
  element: Placed; widget: Widget; title: string; props: CanvasProps; chart?: ChartDefinition
  download?: (format: ChartFileFormat, root: HTMLElement) => void; canImage?: boolean
  drill?: { back: () => void; reset: () => void; depth: number }; children: React.ReactNode
}) {
  const root = useRef<HTMLDivElement>(null)
  const highlighted = props.highlighted?.includes(widget.widget_id) ?? false
  // Untitled items keep their actions in a corner overlay, so a short text box
  // still shows its words while you edit.
  const header = Boolean(title || drill)
  const actions = (className: string) => <div className={className}>
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
  return <div ref={root} data-element-body='' className={cn('relative flex size-full flex-col overflow-hidden',
    highlighted && 'ring-2 ring-brand/60 motion-safe:transition-shadow')} style={boxStyle(element.style)}>
    {header && <div className='flex h-7 shrink-0 items-center justify-between gap-2'>
      <div className='flex min-w-0 items-center gap-1'>
        {drill && <Button variant='ghost' size='icon' className='-ml-1 size-7 shrink-0' aria-label='Back up one level'
          onClick={event => { event.stopPropagation(); drill.back() }}><ArrowLeft className='size-4' /></Button>}
        <h3 className='min-w-0 truncate text-sm font-medium' title={title}>{title}</h3>
        {drill && drill.depth > 1 && <Button variant='link' size='sm' className='h-7 shrink-0 px-1 text-xs text-muted-foreground'
          onClick={event => { event.stopPropagation(); drill.reset() }}>Reset</Button>}
      </div>
      {actions(cn('flex shrink-0 items-center gap-0.5', !props.editing && 'opacity-0 focus-within:opacity-100 group-hover/widget:opacity-100'))}
    </div>}
    {!header && props.editing && actions('absolute top-1 right-1 z-10 flex items-center gap-0.5 rounded-md bg-card opacity-0 shadow-sm focus-within:opacity-100 group-hover/widget:opacity-100 group-data-selected/widget:opacity-100')}
    <div className='flex min-h-0 flex-1 flex-col'>{children}</div>
    {highlighted && <span role='status' className='pointer-events-none absolute top-1 right-1 flex items-center gap-1 rounded-full bg-brand px-2 py-0.5 text-xs text-brand-foreground motion-safe:animate-in motion-safe:fade-in'>
      <Sparkles className='size-3' aria-hidden />Changed by the assistant
    </span>}
  </div>
}

const TEXT_ROLE: Record<NonNullable<TextElement['role']>, string> = {
  title: 'type-display text-2xl font-semibold', heading: 'text-lg font-semibold',
  body: 'text-sm text-muted-foreground', caption: 'text-xs text-muted-foreground',
}

function Runs({ text }: { text: RichText | undefined }) {
  return (text?.runs ?? []).map((run, index) => !('text' in run)
    // A value reads from its chart's result; until that is wired, show that it is missing rather than guess.
    ? <span key={index} aria-label='Value not available'>—</span>
    : run.weight ? <span key={index} style={{ fontWeight: run.weight }}>{run.text}</span> : run.text)
}

function TextBody({ element, widget, props }: { element: TextElement; widget: Widget; props: CanvasProps }) {
  return <WidgetFrame element={element} widget={widget} title={element.title ?? ''} props={props}>
    <p className={cn('min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words', TEXT_ROLE[element.role ?? 'body'])}>
      <Runs text={element.text} />
    </p>
  </WidgetFrame>
}

function gradientEnds(angle: number) {
  const radians = (angle - 90) * Math.PI / 180
  const x = Math.cos(radians) / 2
  const y = Math.sin(radians) / 2
  return { x1: 0.5 - x, y1: 0.5 - y, x2: 0.5 + x, y2: 0.5 + y }
}

/** A shape drawn in screen pixels, so its outline keeps its width at any scale. */
function ShapeBody({ element, w: width, h: height }: { element: ShapeElement; w: number; h: number }) {
  const { scale } = useFrame()
  const w = width * scale
  const h = height * scale
  const style = element.style
  const stroke = style?.stroke
  const sw = stroke && (stroke.width ?? 1) > 0 ? stroke.width ?? 1 : 0
  const fill = style?.fill
  const gradientId = `fill-${element.element_id}`
  const paint = fill && 'start' in fill ? `url(#${CSS.escape(gradientId)})` : fillCss(fill) ?? 'none'
  const common = { fill: paint, stroke: strokeColor(stroke) ?? 'none', strokeWidth: sw, strokeDasharray: dashArray(stroke) }
  const kind = element.shape ?? 'rect'
  const inset = sw / 2
  const pill = kind === 'pill' || (style?.radius ?? 0) >= 9999
  const corner = pill ? Math.min(w, h) / 2 : undefined
  let shape: React.ReactNode
  if (kind === 'ellipse') shape = <ellipse cx={w / 2} cy={h / 2} rx={Math.max(0, w / 2 - inset)} ry={Math.max(0, h / 2 - inset)} {...common} />
  else if (kind === 'triangle') shape = <polygon points={`${w / 2},${inset} ${w - inset},${h - inset} ${inset},${h - inset}`} {...common} />
  else if (kind === 'line') shape = <line x1={0} y1={h / 2} x2={w} y2={h / 2} {...common} stroke={strokeColor(stroke) ?? fillCss(fill) ?? 'var(--foreground)'} strokeWidth={Math.max(1, sw)} />
  else shape = <rect x={inset} y={inset} width={Math.max(0, w - sw)} height={Math.max(0, h - sw)} rx={corner}
    style={corner === undefined ? { rx: themeRadius(style?.radius), ry: themeRadius(style?.radius) } as React.CSSProperties : undefined} {...common} />
  return <svg aria-hidden width={w} height={h} className='block overflow-visible' style={{ opacity: opacityOf(style), filter: dropShadowOf(style) }}>
    {fill && 'start' in fill && <defs><linearGradient id={gradientId} {...gradientEnds(fill.angle ?? 180)}>
      <stop offset='0' style={{ stopColor: `var(--${fill.start})` }} /><stop offset='1' style={{ stopColor: `var(--${fill.end})` }} />
    </linearGradient></defs>}
    {shape}
  </svg>
}

/**
 * A connector between two items (or points), drawn on the page's vector layer.
 * An end that cannot be found, such as a data point that is not resolved,
 * hides the line instead of drawing it somewhere wrong.
 */
function ConnectorView({ element, origin, boxes, props, z }: {
  element: ConnectorElement; origin: { x: number; y: number }; boxes: Map<string, Box>; props: CanvasProps; z: number
}) {
  const { scale, page, selection, select } = useFrame()
  // While items are dragged, lines follow their live boxes.
  const path = connectorPath(element, (useDragView()?.boxes as Map<string, Box> | undefined) ?? boxes)
  if (!path) return null
  const { width, height } = pageSize(page)
  const stroke = element.style?.stroke ?? { token: 'muted-foreground', width: 2, dash: 'solid' as const }
  const sw = stroke.width ?? 2
  const colour = strokeColor(stroke) ?? 'var(--muted-foreground)'
  const selected = selection.has(element.element_id)
  const label = element.name || 'Connector'
  const head = element.head ?? 'arrow'
  return <svg data-element-id={element.element_id} data-kind='connector' className='pointer-events-none absolute overflow-visible'
    style={{ left: -origin.x * scale, top: -origin.y * scale, width: width * scale, height: height * scale, zIndex: z,
      opacity: opacityOf(element.style) }}
    viewBox={`0 0 ${width} ${height}`} preserveAspectRatio='none'
    aria-hidden={!props.editing && element.decorative ? true : undefined}>
    <g role={props.editing || !element.decorative ? 'img' : undefined} aria-label={props.editing || !element.decorative ? label : undefined}
      tabIndex={props.editing ? 0 : undefined} className={cn(props.editing && 'pointer-events-auto cursor-pointer outline-none', element.hidden && 'opacity-40')}
      onFocus={props.editing ? event => { if ((event.currentTarget as Element).matches(':focus-visible')) select(element.element_id, false) } : undefined}
      onClick={props.editing ? event => { event.stopPropagation(); select(element.element_id, event.shiftKey || event.metaKey || event.ctrlKey) } : undefined}>
      {props.editing && <path d={path.d} fill='none' stroke='transparent' strokeWidth={12 / scale} />}
      <path d={path.d} fill='none' stroke={colour} strokeWidth={sw / scale} strokeDasharray={dashArray(stroke)} vectorEffect='non-scaling-stroke'
        strokeLinecap='round' strokeLinejoin='round' />
      {head === 'arrow' && <path d={`M 0 0 L ${-3 - 3 * sw} ${-2 - 1.5 * sw} L ${-3 - 3 * sw} ${2 + 1.5 * sw} Z`} fill={colour}
        transform={`translate(${path.end.x} ${path.end.y}) rotate(${path.angle}) scale(${1 / scale})`} />}
      {head === 'dot' && <circle cx={path.end.x} cy={path.end.y} r={(2 + sw) / scale} fill={colour} />}
      {selected && <path d={path.d} fill='none' stroke='var(--ring)' strokeWidth={(sw + 4) / scale} strokeOpacity={0.35} />}
    </g>
  </svg>
}

/** What a drag in progress shows: its guides and every item's live box (page pixels, spacers included). */
type DragView = { guides: readonly Guide[]; boxes: ReadonlyMap<string, Box> }
type DragStore = { get: () => DragView | null; set: (view: DragView | null) => void; subscribe: (listener: () => void) => () => void }

/**
 * The drag view lives outside React state, so a frame of a drag re-renders only
 * what draws it (guides, selection bounds and lines), not the canvas.
 */
function dragStore(): DragStore {
  let view: DragView | null = null
  const listeners = new Set<() => void>()
  return {
    get: () => view,
    set: next => { view = next; listeners.forEach(listener => listener()) },
    subscribe: listener => { listeners.add(listener); return () => { listeners.delete(listener) } },
  }
}
const DragContext = createContext<DragStore>(dragStore())
const useDragView = () => {
  const store = useContext(DragContext)
  return useSyncExternalStore(store.subscribe, store.get)
}

const FrameContext = createContext<Frame>({
  scale: 1, phone: false, page: { page_id: '', title: '' } as Page, widgets: new Map(),
  selection: new Set(), scope: null, select: () => undefined,
})
const useFrame = () => useContext(FrameContext)

function labelOf(element: Placed, widgets: Map<string, Widget>): string {
  switch (element.kind) {
    case 'visual': return element.title || element.name || 'Chart'
    case 'text': {
      const plain = (element.text?.runs ?? []).map(run => 'text' in run ? run.text : '').join('').trim()
      return element.title || element.name || (plain.length > 60 ? `${plain.slice(0, 57)}…` : plain) || 'Text'
    }
    case 'image': return element.title || element.alt || 'Image'
    case 'filter': return element.title || element.name || 'Filter'
    case 'icon': return element.label || element.name || 'Icon'
    case 'shape': return element.name || 'Shape'
    case 'annotation': return element.name || 'Note'
    case 'group': return element.name || 'Group'
    case 'spacer': return element.title || 'Spacer'
    default: return widgets.get(element.element_id)?.title || 'Item'
  }
}

/** Starts a move (or, from a handle, a resize) of an item whose parent group is `parent`. */
type Gesture = (element: Placed, parent: string | null, mode: 'move' | Edges) => (event: React.PointerEvent) => void
/** Geometry of items being dragged, by element ID, in their parent's coordinates. */
type Live = ReadonlyMap<string, Geometry>

const EDGE_CURSOR: Record<Edges, string> = {
  n: 'cursor-ns-resize', s: 'cursor-ns-resize', e: 'cursor-ew-resize', w: 'cursor-ew-resize',
  ne: 'cursor-nesw-resize', sw: 'cursor-nesw-resize', nw: 'cursor-nwse-resize', se: 'cursor-nwse-resize',
}
const EDGE_PLACE: Record<Edges, string> = {
  nw: '-top-1 -left-1', n: '-top-1 left-1/2 -translate-x-1/2', ne: '-top-1 -right-1', e: 'top-1/2 -right-1 -translate-y-1/2',
  se: '-right-1 -bottom-1', s: '-bottom-1 left-1/2 -translate-x-1/2', sw: '-bottom-1 -left-1', w: 'top-1/2 -left-1 -translate-y-1/2',
}
const ORIGIN = { x: 0, y: 0 }

/** Order siblings for the keyboard and screen readers; stacking stays the saved order. */
function domOrder(elements: readonly PageElement[], page: Page, top: boolean, editing: boolean): PageElement[] {
  const shown = elements.filter(item => editing || !item.hidden)
  const ordered = top ? tabOrder({ ...page, elements: shown }, { includeHidden: true }) : readingOrder(shown.filter(item => !item.decorative))
  const rest = shown.filter(item => !ordered.includes(item))
  return [...ordered, ...rest]
}

function holds(element: PageElement, live: Live): boolean {
  return element.kind === 'group' && element.children.some(child => live.has(child.element_id) || holds(child, live))
}

/**
 * A layer of items. It is memoised, so moving items by translating their nodes
 * renders nothing here; only lines, which read the live boxes, redraw.
 */
const ElementsLayer = memo(function ElementsLayer({ elements, origin, boxes, props, gesture, parent, live }: {
  elements: readonly PageElement[]; origin: { x: number; y: number }; boxes: Map<string, Box>
  props: CanvasProps; gesture: Gesture; parent: string | null; live: Live | null
}) {
  const { page } = useFrame()
  const stack = new Map(elements.map((item, index) => [item.element_id, index + 1]))
  const ordered = useMemo(() => domOrder(elements, page, parent === null, props.editing), [elements, page, parent, props.editing])
  return ordered.map(element => {
    const z = stack.get(element.element_id) ?? 1
    if (element.kind === 'connector') return <ConnectorView key={element.element_id} element={element} origin={origin} boxes={boxes} props={props} z={z} />
    // A note sits on a data point; until it can be located it is shown only while editing.
    if (element.kind === 'annotation' && !props.editing) return null
    const group = element.kind === 'group'
    return <ElementView key={element.element_id} element={element} origin={origin} props={props} gesture={gesture} parent={parent} z={z}
      override={live?.get(element.element_id)} live={group && live && holds(element, live) ? live : null} boxes={group ? boxes : null} />
  })
})

/**
 * One placed item. It is memoised: while something else is dragged it does not
 * redraw, and its content (a chart, say) keeps its own render while only its
 * box moves.
 */
const ElementView = memo(function ElementView({ element, origin, props, gesture, parent, z, override, live, boxes }: {
  element: Placed; origin: { x: number; y: number }; props: CanvasProps; gesture: Gesture
  parent: string | null; z: number; override?: Geometry; live: Live | null; boxes: Map<string, Box> | null
}) {
  const frame = useFrame()
  const { scale, phone, widgets, selection, scope, select } = frame
  const widget = widgets.get(element.element_id)
  const chartLabel = useVisualTitle(element, props.draft)
  const label = element.kind === 'visual' && !element.title ? chartLabel ?? labelOf(element, widgets) : labelOf(element, widgets)
  const editable = props.editing && !phone
  const selected = selection.has(element.element_id)
  const only = selected && selection.size === 1
  // Inside a group, an item answers the pointer once the group is open for editing.
  const reachable = parent === null || parent === scope
  const decorative = element.decorative ?? false
  const box = override ?? element.geometry
  const rotation = box.rotation ?? 0
  const neva = props.neva?.activity.widget_ids.includes(element.element_id) ? props.neva.activity.state : undefined
  const position: React.CSSProperties = phone
    ? element.kind === 'text' ? { maxHeight: 640 } : { height: element.kind === 'icon' ? Math.min(element.geometry.h, 96) : mobileHeight(element) }
    : { left: box.x * scale, top: box.y * scale, width: box.w * scale, height: box.h * scale, zIndex: z,
      transform: rotation ? `rotate(${rotation}deg)` : undefined }
  const shapeSize = element.kind === 'shape' ? `${box.w}x${box.h}` : ''
  const body = useMemo(() => {
    switch (element.kind) {
      case 'visual':
        return widget?.kind === 'chart' ? <ChartBody element={element} widget={widget as ChartWidget} props={props} /> : null
      case 'text':
        return widget ? <TextBody element={element} widget={widget} props={props} /> : null
      case 'image':
        return widget ? <WidgetFrame element={element} widget={widget} title={element.title ?? ''} props={props}>
          <span role='img' aria-label={element.alt} />
        </WidgetFrame> : null
      case 'filter':
      case 'spacer':
        return widget ? <WidgetFrame element={element} widget={widget} title={element.title ?? ''} props={props}><span /></WidgetFrame> : null
      case 'shape': {
        const [w, h] = shapeSize.split('x').map(Number)
        return <ShapeBody element={element} w={w} h={h} />
      }
      case 'icon': {
        const Icon = ICONS[element.icon]
        return <div className='flex size-full items-center justify-center' style={boxStyle({ ...element.style, stroke: null })}>
          <Icon className='size-full' style={{ color: strokeColor(element.style?.stroke) ?? 'var(--foreground)' }}
            strokeWidth={element.style?.stroke?.width || 2} aria-hidden />
        </div>
      }
      case 'annotation':
        return <div className='size-full overflow-hidden border border-dashed border-muted-foreground/60 text-xs text-muted-foreground' style={boxStyle({ ...element.style, stroke: null })}>
          <Runs text={element.text} />
        </div>
      default:
        return null
    }
  }, [element, widget, props, shapeSize])
  const named = !decorative || props.editing
  return <div data-element-id={element.element_id} data-kind={element.kind}
    data-neva-target={neva} data-selected={selected ? '' : undefined}
    className={cn('nova-dataviz-element group/widget', phone ? 'relative w-full shrink-0' : 'absolute',
      editable && reachable && !element.locked && 'cursor-grab active:cursor-grabbing',
      editable && 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
      selected && 'outline-2 outline-offset-2 outline-ring',
      element.kind === 'group' && scope === element.element_id && 'outline-1 outline-offset-4 outline-dashed outline-ring/70',
      element.hidden && 'opacity-40')}
    style={position}
    role={named ? 'group' : undefined}
    aria-hidden={named ? undefined : true}
    aria-label={!named ? undefined : props.editing
      ? `${label}${element.hidden ? ', hidden' : ''}${element.locked ? ', locked' : editable ? ', press Alt and an arrow key to move it' : ''}`
      : label}
    tabIndex={props.editing ? 0 : undefined}
    // Tabbing to an item selects it, unless several items are selected: then Tab
    // passes over items on the way to a command without losing that selection.
    // A press selects through the pointer gesture instead.
    onFocus={props.editing ? event => {
      if (event.target === event.currentTarget && event.currentTarget.matches(':focus-visible') && !selection.has(element.element_id)
        && selection.size <= 1) select(element.element_id, false)
    } : undefined}
    onClick={props.editing && reachable ? event => event.stopPropagation() : undefined}
    onDoubleClick={editable && element.kind === 'group' ? event => {
      event.stopPropagation()
      // Open the group and select the child that was double-clicked.
      let node = (event.target as HTMLElement).closest('[data-element-id]')
      let child: string | null = null
      while (node && node !== event.currentTarget) {
        child = node.getAttribute('data-element-id')
        node = node.parentElement?.closest('[data-element-id]') ?? null
      }
      if (child) select(child, false)
    } : undefined}
    onPointerDown={editable ? gesture(element, parent, 'move') : undefined}>
    {element.kind === 'group'
      ? <div className='absolute inset-0' style={boxStyle(element.style)}>
        <ElementsLayer elements={element.children} origin={{ x: origin.x + box.x, y: origin.y + box.y }} boxes={boxes ?? new Map()}
          props={props} gesture={gesture} parent={element.element_id} live={live} />
      </div>
      : body}
    {editable && element.locked && selected && <Lock aria-hidden className='absolute top-1 left-1 size-3.5 text-muted-foreground' />}
    {editable && only && reachable && !element.locked && HANDLES.map(edge => <span key={edge} aria-hidden data-handle={edge}
      className={cn('absolute z-10 size-2.5 rounded-[2px] border border-ring bg-background', EDGE_PLACE[edge], EDGE_CURSOR[edge])}
      onPointerDown={gesture(element, parent, edge)} />)}
  </div>
})

/** A chart's own title, for an item that does not set one. */
function useVisualTitle(element: Placed, draft: boolean): string | undefined {
  const visual = element.kind === 'visual' ? element : null
  // The same query the chart body reads, so this costs no extra request.
  const chart = useChartDocument(visual?.chart_ref.chart_id ?? '', visual?.chart_ref.revision ?? 0, draft, Boolean(visual && !visual.title))
  return chart.data?.title
}

/** Replace some elements' geometry, at any depth. */
function withGeometry(elements: readonly PageElement[], live: Live): PageElement[] {
  return elements.map(item => {
    const geometry = live.get(item.element_id)
    const moved = geometry ? { ...item, geometry } : item
    return moved.kind === 'group' ? { ...moved, children: withGeometry(moved.children, live) as typeof moved.children } : moved
  })
}

/** Guides, the marquee and the bounds of a multiple selection, over the page. */
function Overlay({ marquee, selection, boxes: saved, scale }: {
  marquee: Box | null; selection: readonly string[]; boxes: ReadonlyMap<string, Box>; scale: number
}) {
  const view = useDragView()
  const guides = view?.guides ?? []
  // Several selected items show one dashed outline around them, following a drag.
  const source = view?.boxes ?? saved
  const chosen = selection.length > 1 ? selection.map(id => source.get(id)).filter((box): box is Box => Boolean(box)) : []
  const around = chosen.length > 1 ? bounds(chosen) : null
  if (!guides.length && !marquee && !around) return null
  return <svg aria-hidden data-testid='canvas-overlay' className='pointer-events-none absolute inset-0 size-full overflow-visible' style={{ zIndex: 1000 }}>
    {around && <rect x={around.x * scale - 4} y={around.y * scale - 4} width={around.w * scale + 8} height={around.h * scale + 8}
      fill='none' stroke='var(--ring)' strokeWidth={1} strokeDasharray='4 3' />}
    {marquee && <rect data-testid='marquee' x={marquee.x * scale} y={marquee.y * scale} width={marquee.w * scale} height={marquee.h * scale}
      fill='color-mix(in oklab, var(--ring) 12%, transparent)' stroke='var(--ring)' strokeWidth={1} />}
    {guides.map((guide, index) => {
      const vertical = guide.axis === 'x'
      if (guide.kind === 'line') {
        return vertical
          ? <line key={index} data-guide='line' x1={guide.at * scale} x2={guide.at * scale} y1={guide.from * scale} y2={guide.to * scale} stroke='var(--brand)' strokeWidth={1} />
          : <line key={index} data-guide='line' y1={guide.at * scale} y2={guide.at * scale} x1={guide.from * scale} x2={guide.to * scale} stroke='var(--brand)' strokeWidth={1} />
      }
      const mid = (guide.from + guide.to) / 2 * scale
      const cx = vertical ? mid : guide.at * scale
      const cy = vertical ? guide.at * scale : mid
      const width = 8 + guide.label.length * 7
      return <g key={index} data-guide='gap'>
        {vertical
          ? <line x1={guide.from * scale} x2={guide.to * scale} y1={cy} y2={cy} stroke='var(--brand)' strokeWidth={1} strokeDasharray='2 2' />
          : <line y1={guide.from * scale} y2={guide.to * scale} x1={cx} x2={cx} stroke='var(--brand)' strokeWidth={1} strokeDasharray='2 2' />}
        <rect x={cx - width / 2} y={cy - 9} width={width} height={18} rx={4} fill='var(--brand)' />
        <text x={cx} y={cy + 4} textAnchor='middle' fontSize={11} fill='var(--brand-foreground)' className='tabular-nums'>{guide.label}</text>
      </g>
    })}
  </svg>
}

/**
 * The dashboard page as a frame: a page of fixed logical size scaled to the
 * space available, with every item placed absolutely at its saved pixels and
 * stacked in saved order. Lines and shapes are vectors. The DOM follows the
 * reading order, so the keyboard and screen readers read top to bottom, left
 * to right (or the page's own tab order). On a phone the page becomes one
 * column in its phone order.
 *
 * In Edit, a press selects (Shift, ⌘ or Ctrl adds to the selection) and a drag
 * on empty page draws a marquee. Selected items move together; one item also
 * resizes from its handles. Moves snap to the grid and to other items' edges
 * and centres with guides (Alt or ⌘ suspends snapping), and commit once per
 * gesture. Double-clicking a group opens it to edit its children.
 */
export function DashboardCanvas(props: CanvasProps) {
  const scroller = useRef<HTMLDivElement | null>(null)
  const host = useRef<HTMLDivElement | null>(null)
  const [available, setAvailable] = useState({ width: 0, height: 0 })
  useLayoutEffect(() => {
    const node = scroller.current
    if (!node) return
    const measure = () => {
      const style = getComputedStyle(node)
      const width = node.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
      const height = node.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)
      setAvailable(current => current.width === width && current.height === height ? current : { width, height })
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  const page = props.page
  // A resize, or a move inside a group, re-renders the items it changes; committed on release.
  const [live, setLive] = useState<Live | null>(null)
  const drag = useMemo(dragStore, [])
  // How far down a drag has reached, in 120-pixel steps, so a scrolling page grows to follow it.
  const [reach, setReach] = useState(0)
  // Nodes still translated after a move, until the page with their new places arrives.
  const translatedNodes = useRef<HTMLElement[]>([])
  const [marquee, setMarquee] = useState<Box | null>(null)
  const phone = available.width > 0 && available.width < PHONE_WIDTH
  const scale = scaleFor(page, available)
  const { width, height } = pageSize(page)
  const grid = frameGrid(page)
  const scrolls = page.canvas?.fit === 'width'
  const elements = page.elements ?? []
  const spacers = useMemo<SpacerItem[]>(() => (page.legacy_spacers ?? []).map(item => ({ ...item, kind: 'spacer', decorative: true })),
    [page.legacy_spacers])
  const widgets = useMemo(() => {
    const { grid: cells, width: across } = pageGrid(page)
    const map = new Map<string, Widget>()
    const walk = (items: readonly PageElement[]) => items.forEach(item => {
      const widget = widgetOf(item, cells, across)
      if (widget) map.set(item.element_id, widget)
      if (item.kind === 'group') walk(item.children)
    })
    walk(page.elements ?? [])
    for (const widget of gridWidgets(page)) if (widget.kind === 'spacer') map.set(widget.widget_id, widget)
    return map
  }, [page])

  // The group open for editing: a double-click opens it, selecting outside it closes it.
  const [scope, setScope] = useState<string | null>(null)
  const parents = useMemo(() => {
    const map = new Map<string, string | null>()
    const walk = (items: readonly PageElement[], parent: string | null) => items.forEach(item => {
      map.set(item.element_id, parent)
      if (item.kind === 'group') walk(item.children, item.element_id)
    })
    walk(elements, null)
    for (const spacer of spacers) map.set(spacer.element_id, null)
    return map
  }, [elements, spacers])
  useEffect(() => {
    const first = props.selection[0]
    if (!first) return
    const parent = parents.get(first) ?? null
    if (parent !== scope && !(parent === null && scope !== null && props.selection.every(id => id === scope))) setScope(parent)
  }, [props.selection, parents, scope])
  useEffect(() => { if (!props.editing) setScope(null) }, [props.editing])

  const selection = useMemo(() => new Set(props.selection), [props.selection])
  const selectionRef = useRef(props.selection)
  selectionRef.current = props.selection
  const onSelectRef = useRef(props.onSelect)
  onSelectRef.current = props.onSelect
  const select = useCallback((id: string, additive: boolean) => {
    const current = selectionRef.current
    onSelectRef.current(additive ? current.includes(id) ? current.filter(item => item !== id) : [...current, id] : [id])
  }, [])
  const frame = useMemo<Frame>(() => ({ scale, phone, page, widgets, selection, scope, select }), [scale, phone, page, widgets, selection, scope, select])

  const liveElements = useMemo(() => live ? withGeometry(elements, live) : elements, [elements, live])
  const boxes = useMemo(() => absoluteBoxes(liveElements), [liveElements])
  useLayoutEffect(() => {
    const nodes = translatedNodes.current.splice(0)
    for (const node of nodes) node.style.translate = ''
    setReach(0)
  }, [page])
  const liveSpacers = live ? spacers.map(item => ({ ...item, geometry: live.get(item.element_id) ?? item.geometry })) : spacers
  // Every item's box with spacers, for the selection outline.
  const allBoxes = useMemo(() => {
    const all = new Map(boxes)
    for (const spacer of liveSpacers) all.set(spacer.element_id, spacer.geometry)
    return all
  }, [boxes, liveSpacers])
  const bottom = Math.max(reach, ...[...allBoxes.values()].map(box => box.y + box.h))
  const logicalHeight = scrolls ? Math.max(height, bottom) + (props.editing ? EDIT_ROOM : 0) : height

  const scaleRef = useRef(scale)
  scaleRef.current = scale
  const pageRef = useRef(page)
  pageRef.current = page
  const scopeRef = useRef(scope)
  scopeRef.current = scope
  const arrange = useRef(props.onArrange)
  useEffect(() => { arrange.current = props.onArrange }, [props.onArrange])
  const suppressClick = useRef(false)

  const gesture = useCallback<Gesture>((element, parent, mode) => event => {
    if (event.button !== 0) return
    if (mode === 'move' && (event.target as HTMLElement).closest('button, a, input, select, textarea, [role="menuitem"], [data-handle]')) return
    // A closed group's child lets the press reach the group.
    if (parent !== null && parent !== scopeRef.current) return
    event.stopPropagation()
    const id = element.element_id
    if (mode === 'move' && (event.shiftKey || event.metaKey || event.ctrlKey)) {
      const current = selectionRef.current
      onSelectRef.current(current.includes(id) ? current.filter(item => item !== id) : [...current, id])
      return
    }
    let chosen: readonly string[] = selectionRef.current
    if (!chosen.includes(id) || mode !== 'move') {
      chosen = [id]
      onSelectRef.current([id])
    }
    const current = pageRef.current
    const found = index(current)
    const spacersNow = current.legacy_spacers ?? []
    const geometryOf = (item: string) => found.get(item)?.element.geometry ?? spacersNow.find(spacer => spacer.element_id === item)?.geometry
    const lockedOf = (item: string) => found.get(item)?.element.locked ?? spacersNow.find(spacer => spacer.element_id === item)?.locked ?? false
    const parentOf = (item: string) => found.get(item)?.parent ?? null
    // Items selected together move together; locked ones and ones in another group stay.
    const moving = chosen.filter(item => parentOf(item) === parent && !lockedOf(item) && geometryOf(item))
    if (!moving.length) return
    const origins = new Map(moving.map(item => [item, geometryOf(item)!]))
    const before = bounds([...origins.values()])
    const start = { x: event.clientX, y: event.clientY }
    const gridNow = frameGrid(current)
    const { width: across, height: down } = pageSize(current)
    const others = [...otherBoxes(current, new Set(moving)), ...spacersNow.filter(item => !origins.has(item.element_id)).map(item => item.geometry)]
    const nested = parent !== null
    // A move on the page translates the moving nodes each frame: no item re-renders, so
    // a drag costs the same with 6 items or 60. Lines and guides still render.
    const translate = mode === 'move' && !nested
    let nodes: [HTMLElement, Geometry][] | null = null
    const release = () => { for (const [node] of nodes ?? []) node.style.translate = '' }
    let moved = false
    let last: PointerEvent | null = null
    let frameId = 0
    let result: { geometry: Map<string, Geometry>; guides: Guide[] } | null = null
    const compute = (next: PointerEvent) => {
      const k = scaleRef.current || 1
      const dx = (next.clientX - start.x) / k
      const dy = (next.clientY - start.y) / k
      const free = next.altKey || next.metaKey
      const context = { grid: gridNow, width: across, height: down, others, threshold: SNAP_DISTANCE,
        snapGrid: !free && !nested && (current.canvas?.grid?.snap ?? true),
        snapElements: !free && !nested && (current.canvas?.grid?.snap_to_elements ?? true) }
      const candidate = mode === 'move' ? { ...before, x: before.x + dx, y: before.y + dy } : resizeBox(before, mode, dx, dy)
      const snapped = context.snapGrid || context.snapElements ? smartSnap(candidate, context, mode === 'move' ? 'move' : { edges: mode }) : { box: candidate, guides: [] }
      const box = clampBox(snapped.box, current, { nested, resize: mode !== 'move' })
      const geometry = new Map<string, Geometry>()
      for (const [item, origin] of origins) {
        geometry.set(item, mode === 'move'
          ? toGeometry({ x: origin.x + box.x - before.x, y: origin.y + box.y - before.y, w: origin.w, h: origin.h }, origin.rotation ?? 0)
          : toGeometry(box, origin.rotation ?? 0))
      }
      return { geometry, guides: snapped.guides }
    }
    const draw = () => {
      frameId = 0
      if (!last) return
      result = compute(last)
      const shown = result
      if (translate) {
        nodes ??= moving.flatMap(item => {
          const node = host.current?.querySelector<HTMLElement>(`[data-element-id="${CSS.escape(item)}"]`)
          return node && node.dataset.kind !== 'connector' ? [[node, origins.get(item)!] as [HTMLElement, Geometry]] : []
        })
        const k = scaleRef.current || 1
        for (const [node, origin] of nodes) {
          const geometry = shown.geometry.get(node.dataset.elementId!)!
          node.style.translate = `${(geometry.x - origin.x) * k}px ${(geometry.y - origin.y) * k}px`
        }
      }
      const boxesNow = absoluteBoxes(withGeometry(current.elements ?? [], shown.geometry))
      for (const spacer of spacersNow) boxesNow.set(spacer.element_id, shown.geometry.get(spacer.element_id) ?? spacer.geometry)
      const lowest = Math.max(0, ...[...shown.geometry.keys()].map(item => { const box = boxesNow.get(item); return box ? box.y + box.h : 0 }))
      // One render per animation frame, committed in that frame: guides and lines,
      // the page height in 120-pixel steps, and the changed items unless they were translated.
      flushSync(() => {
        drag.set({ guides: shown.guides, boxes: boxesNow })
        setReach(current => Math.max(current, Math.ceil(lowest / 120) * 120))
        if (!translate) setLive(shown.geometry)
      })
    }
    const move = (next: PointerEvent) => {
      if (!moved && Math.hypot(next.clientX - start.x, next.clientY - start.y) < DRAG_START) return
      moved = true
      last = next
      if (!frameId) frameId = requestAnimationFrame(draw)
    }
    const finish = (commit: boolean) => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', cancel)
      window.removeEventListener('keydown', escape, true)
      if (frameId) cancelAnimationFrame(frameId)
      setLive(null)
      drag.set(null)
      if (!commit || !moved || !last) return release()
      const final = compute(last)
      const placements = [...final.geometry].filter(([item, geometry]) => !sameBox(geometry, origins.get(item)!))
        .map(([item, geometry]) => ({ element_id: item, geometry }))
      if (!placements.length) return release()
      if (nodes) {
        // Keep the nodes where they were dropped until the moved page renders, then let go.
        const k = scaleRef.current || 1
        for (const [node, origin] of nodes) {
          const geometry = final.geometry.get(node.dataset.elementId!)!
          node.style.translate = `${(geometry.x - origin.x) * k}px ${(geometry.y - origin.y) * k}px`
        }
        translatedNodes.current.push(...nodes.map(([node]) => node))
      }
      arrange.current(placements)
    }
    const up = () => finish(true)
    const cancel = () => finish(false)
    const escape = (key: KeyboardEvent) => {
      if (key.key !== 'Escape') return
      key.preventDefault()
      key.stopImmediatePropagation()
      finish(false)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', cancel)
    window.addEventListener('keydown', escape, true)
  }, [drag])

  const pointAt = (event: { clientX: number; clientY: number }) => {
    const rect = host.current?.getBoundingClientRect()
    if (!rect?.width) return null
    return { x: (event.clientX - rect.left) / scale, y: (event.clientY - rect.top) / scale }
  }

  // A drag on empty page draws a marquee that selects what it touches.
  const startMarquee = (event: React.PointerEvent) => {
    if (!props.editing || phone || event.button !== 0) return
    if ((event.target as HTMLElement).closest('[data-element-id], button, a, input')) return
    const origin = pointAt(event)
    if (!origin) return
    const additive = event.shiftKey || event.metaKey || event.ctrlKey
    const startSelection = selectionRef.current
    const startScope = scopeRef.current
    let rect: Box | null = null
    const move = (next: PointerEvent) => {
      const point = pointAt(next)
      if (!point) return
      rect = { x: Math.min(origin.x, point.x), y: Math.min(origin.y, point.y), w: Math.abs(point.x - origin.x), h: Math.abs(point.y - origin.y) }
      if (rect.w * scaleRef.current < DRAG_START && rect.h * scaleRef.current < DRAG_START) return
      setMarquee(rect)
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setMarquee(null)
      if (!rect || (rect.w * scaleRef.current < DRAG_START && rect.h * scaleRef.current < DRAG_START)) return
      suppressClick.current = true
      const current = pageRef.current
      const found = index(current)
      const area = rect
      const candidates = [...found.values()].filter(item => item.parent === startScope).map(item => ({ id: item.element.element_id, box: item.box }))
      if (startScope === null) for (const spacer of current.legacy_spacers ?? []) candidates.push({ id: spacer.element_id, box: spacer.geometry })
      const hits = candidates.filter(({ box }) => box.x < area.x + area.w && area.x < box.x + box.w && box.y < area.y + area.h && area.y < box.y + box.h)
        .map(item => item.id)
      onSelectRef.current(additive ? [...startSelection, ...hits.filter(id => !startSelection.includes(id))] : [...hits])
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  // A toolbar item dragged over the page shows where it will land.
  const [drop, setDrop] = useState<Layout | null>(null)
  const dropping = props.editing && !phone && Boolean(props.onDropWidget)
  const cells = pageGrid(page)
  const cellAt = (event: { clientX: number; clientY: number }) => {
    const point = pointAt(event)
    if (!point) return null
    return { x: Math.max(0, Math.floor(point.x / (width / 12))), y: Math.max(0, Math.floor(point.y / ROW_PX)) }
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
    const next = slotAt(gridWidgets(page), size.w, size.h, cell)
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
  const dropBox = drop ? cellRect(drop, cells.grid, cells.width) : null
  const columns = props.editing && !phone && (page.canvas?.grid?.show ?? true) ? columnEdges(grid, width) : null

  return <DragContext.Provider value={drag}><FrameContext.Provider value={frame}>
    <div ref={scroller} data-canvas-scroll='' className={cn('min-h-0 flex-1 p-2.5', scrolls || phone ? 'overflow-y-auto' : 'overflow-auto')}
      onClick={() => {
        if (suppressClick.current) { suppressClick.current = false; return }
        if (props.editing) { props.onSelect([]); setScope(null) }
      }}>
      {props.editing && phone && <p role='status' className='m-1.5 rounded-md border border-dashed p-2 text-xs text-muted-foreground'>
        Widen the window to move or resize items. A phone shows this page as one column, in reading order.
      </p>}
      {!elements.length && !spacers.length && <div className='m-1.5 flex flex-col items-center gap-2 rounded-lg border border-dashed px-6 py-12 text-center'>
        <p className='text-sm font-medium'>This page is empty</p>
        <p className='max-w-sm text-sm text-muted-foreground'>{props.editing ? 'Add a chart, KPI or text from the toolbar, or ask the assistant to build one for you.' : 'Nothing has been added to this page yet.'}</p>
      </div>}
      {phone
        ? <div ref={host} role='group' aria-label={`${page.title} canvas`} data-mode={props.editing ? 'edit' : 'view'} data-layout='phone'
          className='nova-dataviz-canvas flex flex-col gap-3 p-1.5'>
          {mobileOrder(page).map(element => <ElementView key={element.element_id} element={element} origin={ORIGIN}
            props={props} gesture={gesture} parent={null} z={1} live={null} boxes={element.kind === 'group' ? boxes : null} />)}
        </div>
        : <div data-canvas-frame='' className={cn('relative isolate mx-auto shrink-0', !scrolls && 'rounded-sm ring-1 ring-surface-border')}
          style={{ width: width * scale, height: logicalHeight * scale, background: fillCss(page.canvas?.background) }}
          onPointerDown={props.editing ? startMarquee : undefined}
          onDragOver={dropping ? onDragOver : undefined} onDrop={dropping ? onDrop : undefined}
          onDragLeave={dropping ? event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDrop(null) } : undefined}
          onPointerMove={props.onPointer ? event => {
            const point = pointAt(event)
            if (point) props.onPointer?.({ x: point.x / width, y: point.y / ROW_PX })
          } : undefined}
          onPointerLeave={props.onPointer ? () => props.onPointer?.(null) : undefined}>
          {columns && <div aria-hidden data-testid='canvas-grid' className='nova-dataviz-grid'>
            {columns.starts.map((start, index) => <span key={index}
              style={{ left: start * scale, width: (columns.ends[index] - start) * scale }} />)}
          </div>}
          <div ref={host} role='group' aria-label={`${page.title} canvas`} data-mode={props.editing ? 'edit' : 'view'} data-layout='frame'
            className='nova-dataviz-canvas absolute inset-0 isolate'>
            <ElementsLayer elements={elements} origin={ORIGIN} boxes={boxes} props={props} gesture={gesture} parent={null} live={live} />
            {spacers.map((item, index) => <ElementView key={item.element_id} element={item} origin={ORIGIN} override={live?.get(item.element_id)}
              props={props} gesture={gesture} parent={null} z={elements.length + index + 1} live={null} boxes={null} />)}
          </div>
          <Overlay marquee={marquee} selection={props.editing ? props.selection : []} boxes={allBoxes} scale={scale} />
          {dropBox && <div aria-hidden data-testid='drop-preview' className='nova-dataviz-drop'
            style={{ left: dropBox.x * scale, top: dropBox.y * scale, width: dropBox.w * scale, height: dropBox.h * scale }} />}
          {props.collaborators?.length ? <PresenceCursors others={props.collaborators} pageId={page.page_id} scale={scale} /> : null}
          {props.neva && (!props.neva.activity.page_id || props.neva.activity.page_id === page.page_id)
            ? <NevaPointer activity={props.neva.activity} short={props.neva.short} host={host} /> : null}
        </div>}
    </div>
  </FrameContext.Provider></DragContext.Provider>
}
