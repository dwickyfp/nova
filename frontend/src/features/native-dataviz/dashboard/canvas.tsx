import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
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
  absoluteBoxes, boxStyle, clampBox, findElement, columnEdges, connectorPath, dashArray, fillCss, frameGrid, HANDLES, mobileHeight,
  mobileOrder, opacityOf, PHONE_WIDTH, themeRadius, pageSize, readingOrder, resizeBox, sameBox, scaleFor, snapBox, strokeColor, tabOrder,
  toGeometry, type Box, type Edges,
} from './frame'
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
  selectedId: string | null
  onSelect: (elementId: string | null) => void
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

/** How the page is drawn right now: its scale, and whether it is the phone column. */
type Frame = { scale: number; phone: boolean; page: Page; widgets: Map<string, Widget> }

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
  return <div ref={root} data-element-body='' className={cn('relative flex size-full flex-col overflow-hidden',
    highlighted && 'ring-2 ring-brand/60 motion-safe:transition-shadow')} style={boxStyle(element.style)}>
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
function ShapeBody({ element }: { element: ShapeElement }) {
  const { scale } = useFrame()
  const w = element.geometry.w * scale
  const h = element.geometry.h * scale
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
  return <svg aria-hidden width={w} height={h} className='block overflow-visible' style={{ opacity: opacityOf(style) }}>
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
  const { scale, page } = useFrame()
  const path = connectorPath(element, boxes)
  if (!path) return null
  const { width, height } = pageSize(page)
  const stroke = element.style?.stroke ?? { token: 'muted-foreground', width: 2, dash: 'solid' as const }
  const sw = stroke.width ?? 2
  const colour = strokeColor(stroke) ?? 'var(--muted-foreground)'
  const selected = props.selectedId === element.element_id
  const label = element.name || 'Connector'
  const head = element.head ?? 'arrow'
  return <svg data-element-id={element.element_id} data-kind='connector' className='pointer-events-none absolute overflow-visible'
    style={{ left: -origin.x * scale, top: -origin.y * scale, width: width * scale, height: height * scale, zIndex: z,
      opacity: opacityOf(element.style) }}
    viewBox={`0 0 ${width} ${height}`} preserveAspectRatio='none'
    aria-hidden={!props.editing && element.decorative ? true : undefined}>
    <g role={props.editing || !element.decorative ? 'img' : undefined} aria-label={props.editing || !element.decorative ? label : undefined}
      tabIndex={props.editing ? 0 : undefined} className={cn(props.editing && 'pointer-events-auto cursor-pointer outline-none', element.hidden && 'opacity-40')}
      onFocus={props.editing ? () => props.onSelect(element.element_id) : undefined}
      onClick={props.editing ? event => { event.stopPropagation(); props.onSelect(element.element_id) } : undefined}>
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

const FrameContext = createContext<Frame>({ scale: 1, phone: false, page: { page_id: '', title: '' } as Page, widgets: new Map() })
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

type Gesture = (element: Placed, nested: boolean, mode: 'move' | Edges) => (event: React.PointerEvent) => void

const EDGE_CURSOR: Record<Edges, string> = {
  n: 'cursor-ns-resize', s: 'cursor-ns-resize', e: 'cursor-ew-resize', w: 'cursor-ew-resize',
  ne: 'cursor-nesw-resize', sw: 'cursor-nesw-resize', nw: 'cursor-nwse-resize', se: 'cursor-nwse-resize',
}
const EDGE_PLACE: Record<Edges, string> = {
  nw: '-top-1 -left-1', n: '-top-1 left-1/2 -translate-x-1/2', ne: '-top-1 -right-1', e: 'top-1/2 -right-1 -translate-y-1/2',
  se: '-right-1 -bottom-1', s: '-bottom-1 left-1/2 -translate-x-1/2', sw: '-bottom-1 -left-1', w: 'top-1/2 -left-1 -translate-y-1/2',
}

/** Order siblings for the keyboard and screen readers; stacking stays the saved order. */
function domOrder(elements: readonly PageElement[], page: Page, top: boolean, editing: boolean): PageElement[] {
  const shown = elements.filter(item => editing || !item.hidden)
  const ordered = top ? tabOrder({ ...page, elements: shown }, { includeHidden: true }) : readingOrder(shown.filter(item => !item.decorative))
  const rest = shown.filter(item => !ordered.includes(item))
  return [...ordered, ...rest]
}

function ElementsLayer({ elements, origin, boxes, props, gesture, top }: {
  elements: readonly PageElement[]; origin: { x: number; y: number }; boxes: Map<string, Box>
  props: CanvasProps; gesture: Gesture; top: boolean
}) {
  const { page } = useFrame()
  const stack = new Map(elements.map((item, index) => [item.element_id, index + 1]))
  return domOrder(elements, page, top, props.editing).map(element => {
    const z = stack.get(element.element_id) ?? 1
    if (element.kind === 'connector') return <ConnectorView key={element.element_id} element={element} origin={origin} boxes={boxes} props={props} z={z} />
    // A note sits on a data point; until it can be located it is shown only while editing.
    if (element.kind === 'annotation' && !props.editing) return null
    return <ElementView key={element.element_id} element={element} origin={origin} boxes={boxes} props={props} gesture={gesture} nested={!top} z={z} />
  })
}

function ElementView({ element, origin, boxes, props, gesture, nested, z }: {
  element: Placed; origin: { x: number; y: number }; boxes: Map<string, Box>; props: CanvasProps
  gesture: Gesture; nested: boolean; z: number
}) {
  const frame = useFrame()
  const { scale, phone, widgets } = frame
  const widget = widgets.get(element.element_id)
  const chartLabel = useVisualTitle(element, props.draft)
  const label = element.kind === 'visual' && !element.title ? chartLabel ?? labelOf(element, widgets) : labelOf(element, widgets)
  const editable = props.editing && !phone
  const selected = props.selectedId === element.element_id
  const decorative = element.decorative ?? false
  const box = element.geometry
  const rotation = box.rotation ?? 0
  const neva = props.neva?.activity.widget_ids.includes(element.element_id) ? props.neva.activity.state : undefined
  const position: React.CSSProperties = phone
    ? element.kind === 'text' ? { maxHeight: 640 } : { height: element.kind === 'icon' ? Math.min(element.geometry.h, 96) : mobileHeight(element) }
    : { left: box.x * scale, top: box.y * scale, width: box.w * scale, height: box.h * scale, zIndex: z,
      transform: rotation ? `rotate(${rotation}deg)` : undefined }
  let body: React.ReactNode
  switch (element.kind) {
    case 'visual':
      body = widget?.kind === 'chart' ? <ChartBody element={element} widget={widget as ChartWidget} props={props} /> : null
      break
    case 'text':
      body = widget ? <TextBody element={element} widget={widget} props={props} /> : null
      break
    case 'image':
      body = widget ? <WidgetFrame element={element} widget={widget} title={element.title ?? ''} props={props}>
        <span role='img' aria-label={element.alt} />
      </WidgetFrame> : null
      break
    case 'filter':
      body = widget ? <WidgetFrame element={element} widget={widget} title={element.title ?? ''} props={props}><span /></WidgetFrame> : null
      break
    case 'shape':
      body = <ShapeBody element={element} />
      break
    case 'icon': {
      const Icon = ICONS[element.icon]
      const style = boxStyle({ ...element.style, stroke: null })
      body = <div className='flex size-full items-center justify-center' style={style}>
        <Icon className='size-full' style={{ color: strokeColor(element.style?.stroke) ?? 'var(--foreground)' }}
          strokeWidth={element.style?.stroke?.width || 2} aria-hidden />
      </div>
      break
    }
    case 'annotation':
      body = <div className='size-full overflow-hidden border border-dashed border-muted-foreground/60 text-xs text-muted-foreground' style={boxStyle({ ...element.style, stroke: null })}>
        <Runs text={element.text} />
      </div>
      break
    case 'spacer':
      body = widget ? <WidgetFrame element={element} widget={widget} title={element.title ?? ''} props={props}><span /></WidgetFrame> : null
      break
    case 'group': {
      const inner = { x: origin.x + box.x, y: origin.y + box.y }
      body = <div className='absolute inset-0' style={boxStyle(element.style)}>
        <ElementsLayer elements={element.children} origin={inner} boxes={boxes} props={props} gesture={gesture} top={false} />
      </div>
      break
    }
  }
  const named = !decorative || props.editing
  return <div data-element-id={element.element_id} data-kind={element.kind}
    data-neva-target={neva}
    className={cn('nova-dataviz-element group/widget', phone ? 'relative w-full shrink-0' : 'absolute',
      editable && !element.locked && 'cursor-grab active:cursor-grabbing',
      editable && 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
      selected && 'outline-2 outline-offset-2 outline-ring',
      element.hidden && 'opacity-40')}
    style={position}
    role={named ? 'group' : undefined}
    aria-hidden={named ? undefined : true}
    aria-label={!named ? undefined : props.editing
      ? `${label}${element.hidden ? ', hidden' : ''}${element.locked ? ', locked' : editable ? ', press Alt and an arrow key to move it' : ''}`
      : label}
    tabIndex={props.editing ? 0 : undefined}
    onFocus={props.editing ? event => { if (event.target === event.currentTarget) props.onSelect(element.element_id) } : undefined}
    onClick={props.editing ? event => { event.stopPropagation(); props.onSelect(element.element_id) } : undefined}
    onPointerDown={editable ? gesture(element, nested, 'move') : undefined}>
    {body}
    {editable && element.locked && selected && <Lock aria-hidden className='absolute top-1 left-1 size-3.5 text-muted-foreground' />}
    {editable && selected && !element.locked && HANDLES.map(edge => <span key={edge} aria-hidden data-handle={edge}
      className={cn('absolute z-10 size-2.5 rounded-[2px] border border-ring bg-background', EDGE_PLACE[edge], EDGE_CURSOR[edge])}
      onPointerDown={gesture(element, nested, edge)} />)}
  </div>
}

/** A chart's own title, for an item that does not set one. */
function useVisualTitle(element: Placed, draft: boolean): string | undefined {
  const visual = element.kind === 'visual' ? element : null
  // The same query the chart body reads, so this costs no extra request.
  const chart = useChartDocument(visual?.chart_ref.chart_id ?? '', visual?.chart_ref.revision ?? 0, draft, Boolean(visual && !visual.title))
  return chart.data?.title
}

/** Replace one element's geometry, at any depth. */
function withGeometry(elements: readonly PageElement[], id: string, geometry: Geometry): PageElement[] {
  return elements.map(item => item.element_id === id ? { ...item, geometry }
    : item.kind === 'group' ? { ...item, children: withGeometry(item.children, id, geometry) as typeof item.children } : item)
}

/**
 * The dashboard page as a frame: a page of fixed logical size scaled to the
 * space available, with every item placed absolutely at its saved pixels and
 * stacked in saved order. Lines and shapes are vectors. The DOM follows the
 * reading order, so the keyboard and screen readers read top to bottom, left
 * to right (or the page's own tab order). On a phone the page becomes one
 * column in its phone order. In Edit, items move and resize by pointer, snap
 * to the page grid (Alt or ⌘ suspends snapping) and commit once per gesture.
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

  // A drag or resize in progress, shown live and committed on release.
  const [live, setLive] = useState<{ id: string; geometry: Geometry } | null>(null)
  const page = useMemo(() => live ? {
    ...props.page, elements: withGeometry(props.page.elements ?? [], live.id, live.geometry),
    legacy_spacers: props.page.legacy_spacers?.map(item => item.element_id === live.id ? { ...item, geometry: live.geometry } : item),
  } : props.page, [props.page, live])
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
  const frame = useMemo<Frame>(() => ({ scale, phone, page, widgets }), [scale, phone, page, widgets])
  const boxes = useMemo(() => absoluteBoxes(elements), [elements])
  const bottom = Math.max(0, ...[...boxes.values(), ...spacers.map(item => item.geometry)].map(box => box.y + box.h))
  const logicalHeight = scrolls ? Math.max(height, bottom) + (props.editing ? EDIT_ROOM : 0) : height

  const scaleRef = useRef(scale)
  scaleRef.current = scale
  const pageRef = useRef(props.page)
  pageRef.current = props.page
  const arrange = useRef(props.onArrange)
  useEffect(() => { arrange.current = props.onArrange }, [props.onArrange])
  const onSelect = props.onSelect
  const gesture = useCallback<Gesture>((element, nested, mode) => event => {
    if (event.button !== 0) return
    if (mode === 'move' && (event.target as HTMLElement).closest('button, a, input, select, textarea, [role="menuitem"], [data-handle]')) return
    event.stopPropagation()
    onSelect(element.element_id)
    if (element.locked) return
    const current = pageRef.current
    const original = findElement(current.elements ?? [], element.element_id)?.geometry
      ?? current.legacy_spacers?.find(item => item.element_id === element.element_id)?.geometry ?? element.geometry
    const origin: Box = { x: original.x, y: original.y, w: original.w, h: original.h }
    const start = { x: event.clientX, y: event.clientY }
    const snapOn = current.canvas?.grid?.snap ?? true
    const pageGridNow = frameGrid(current)
    const across = pageSize(current).width
    let moved = false
    let box = origin
    const move = (next: PointerEvent) => {
      const dx = next.clientX - start.x
      const dy = next.clientY - start.y
      if (!moved && Math.hypot(dx, dy) < DRAG_START) return
      moved = true
      const k = scaleRef.current || 1
      let candidate = mode === 'move' ? { ...origin, x: origin.x + dx / k, y: origin.y + dy / k } : resizeBox(origin, mode, dx / k, dy / k)
      if (snapOn && !nested && !(next.altKey || next.metaKey)) {
        candidate = snapBox(candidate, pageGridNow, across, mode === 'move' ? 'move' : { edges: mode })
      }
      box = clampBox(candidate, current, { nested, resize: mode !== 'move' })
      setLive({ id: element.element_id, geometry: toGeometry(box, original.rotation ?? 0) })
    }
    const finish = (commit: boolean) => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', cancel)
      window.removeEventListener('keydown', escape, true)
      setLive(null)
      if (commit && moved && !sameBox(box, origin)) {
        arrange.current([{ element_id: element.element_id, geometry: toGeometry(box, original.rotation ?? 0) }])
      }
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
  }, [onSelect])

  // A toolbar item dragged over the page shows where it will land.
  const [drop, setDrop] = useState<Layout | null>(null)
  const dropping = props.editing && !phone && Boolean(props.onDropWidget)
  const cells = pageGrid(page)
  const pointAt = (event: { clientX: number; clientY: number }) => {
    const rect = host.current?.getBoundingClientRect()
    if (!rect?.width) return null
    return { x: (event.clientX - rect.left) / scale, y: (event.clientY - rect.top) / scale }
  }
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

  return <FrameContext.Provider value={frame}>
    <div ref={scroller} data-canvas-scroll='' className={cn('min-h-0 flex-1 p-2.5', scrolls || phone ? 'overflow-y-auto' : 'overflow-auto')}
      onClick={() => props.editing && props.onSelect(null)}>
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
          {mobileOrder(page).map(element => <ElementView key={element.element_id} element={element} origin={{ x: 0, y: 0 }} boxes={boxes}
            props={props} gesture={gesture} nested={false} z={1} />)}
        </div>
        : <div data-canvas-frame='' className={cn('relative mx-auto shrink-0', !scrolls && 'rounded-sm ring-1 ring-surface-border')}
          style={{ width: width * scale, height: logicalHeight * scale, background: fillCss(page.canvas?.background) }}
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
            <ElementsLayer elements={elements} origin={{ x: 0, y: 0 }} boxes={boxes} props={props} gesture={gesture} top />
            {spacers.map((item, index) => <ElementView key={item.element_id} element={item} origin={{ x: 0, y: 0 }} boxes={boxes}
              props={props} gesture={gesture} nested={false} z={elements.length + index + 1} />)}
          </div>
          {dropBox && <div aria-hidden data-testid='drop-preview' className='nova-dataviz-drop'
            style={{ left: dropBox.x * scale, top: dropBox.y * scale, width: dropBox.w * scale, height: dropBox.h * scale }} />}
          {props.collaborators?.length ? <PresenceCursors others={props.collaborators} pageId={page.page_id} scale={scale} /> : null}
          {props.neva && (!props.neva.activity.page_id || props.neva.activity.page_id === page.page_id)
            ? <NevaPointer activity={props.neva.activity} short={props.neva.short} host={host} /> : null}
        </div>}
    </div>
  </FrameContext.Provider>
}
