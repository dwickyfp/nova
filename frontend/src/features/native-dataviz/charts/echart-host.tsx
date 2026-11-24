import { useEffect, useRef, useState } from 'react'
import type { ECharts, EChartsCoreOption } from 'echarts/core'
import { readLightToken, readToken } from '@/lib/read-token'
import { cn } from '@/lib/utils'

export type VizTokens = {
  series: string[]
  tones: string[]
  mark: string
  highlight: string
  text: string
  muted: string
  grid: string
  axis: string
  surface: string
  reference: string
}

/** A clicked mark, with where it was clicked inside the chart, in CSS pixels. */
export type MarkSelection = {
  seriesIndex: number; dataIndex: number; name: string; x?: number; y?: number
  /** Shift, Ctrl or Cmd was held: add to the selection instead of replacing it. */
  additive?: boolean
  /** The click was on a legend entry: `name` is the series. */
  legend?: boolean
}

/** Chart colours from theme.css: the page's current theme, or light for printing. */
export function readTokens(mode: 'current' | 'light' = 'current'): VizTokens {
  // theme.css owns every color; tokens are always defined where charts render.
  const token = (name: string) => mode === 'light' ? readLightToken(name, '') : readToken(name, '')
  return {
    series: [1, 2, 3, 4, 5, 6, 7, 8, 9].map(index => token(`--chart-${index}`)),
    tones: [1, 2, 3, 4, 5, 6].map(index => token(`--chart-tone-${index}`)),
    mark: token('--viz-mark'),
    highlight: token('--viz-highlight'),
    text: token('--foreground'),
    muted: token('--muted-foreground'),
    grid: token('--viz-grid'),
    axis: token('--viz-axis'),
    surface: token('--card'),
    reference: token('--viz-reference'),
  }
}

function useThemeVersion(): number {
  const [version, setVersion] = useState(0)
  useEffect(() => {
    const observer = new MutationObserver(() => setVersion(value => value + 1))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])
  return version
}

let modules: Promise<typeof import('echarts/core')> | null = null

export function loadEcharts() {
  // Registered once per page; only the marks native charts draw are bundled.
  modules ??= Promise.all([
    import('echarts/core'), import('echarts/charts'), import('echarts/components'), import('echarts/renderers'),
  ]).then(([core, charts, components, renderers]) => {
    core.use([
      charts.BarChart, charts.LineChart, charts.PieChart, charts.ScatterChart, charts.HeatmapChart,
      charts.FunnelChart, charts.TreemapChart, charts.SankeyChart, charts.GaugeChart, charts.BoxplotChart,
      components.GridComponent, components.TooltipComponent, components.LegendComponent,
      components.VisualMapComponent, components.MarkLineComponent, components.MarkAreaComponent,
      components.DataZoomComponent, components.AriaComponent, renderers.CanvasRenderer,
    ])
    return core
  })
  return modules
}

/**
 * One Apache ECharts instance. Options are rebuilt from theme tokens whenever the
 * theme changes, and the instance follows its container's size.
 */
export function EChartHost({ build, label, onSelect, className }: {
  /** Receives the drawing width, rounded so small resizes do not rebuild. */
  build: (tokens: VizTokens, width: number) => EChartsCoreOption
  label: string
  onSelect?: (selection: MarkSelection) => void
  className?: string
}) {
  const container = useRef<HTMLDivElement | null>(null)
  const instance = useRef<ECharts | null>(null)
  const select = useRef(onSelect)
  const [failed, setFailed] = useState(false)
  const [width, setWidth] = useState(0)
  const theme = useThemeVersion()
  useEffect(() => { select.current = onSelect }, [onSelect])

  useEffect(() => {
    let cancelled = false
    let observer: ResizeObserver | null = null
    void loadEcharts().then(core => {
      const node = container.current
      if (cancelled || !node) return
      const chart = core.init(node, undefined, { renderer: 'canvas' })
      instance.current = chart
      chart.on('click', params => {
        const event = params as { seriesIndex?: number; dataIndex?: number; name?: string
          event?: { offsetX?: number; offsetY?: number; event?: { shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean } } }
        const keys = event.event?.event
        if (typeof event.dataIndex === 'number') {
          select.current?.({ seriesIndex: event.seriesIndex ?? 0, dataIndex: event.dataIndex, name: String(event.name ?? ''),
            x: event.event?.offsetX, y: event.event?.offsetY, additive: Boolean(keys?.shiftKey || keys?.ctrlKey || keys?.metaKey) })
        }
      })
      // While the chart can filter, a legend entry selects its group instead of hiding it.
      chart.on('legendselectchanged', params => {
        const { name } = params as { name: string }
        if (!select.current) return
        chart.dispatchAction({ type: 'legendSelect', name })
        select.current({ seriesIndex: -1, dataIndex: -1, name, legend: true })
      })
      observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => {
        chart.resize()
        setWidth(Math.round(node.clientWidth / 16) * 16)
      })
      observer?.observe(node)
      setFailed(false)
    }).catch(() => { if (!cancelled) setFailed(true) })
    return () => {
      cancelled = true
      observer?.disconnect()
      instance.current?.dispose()
      instance.current = null
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    void loadEcharts().then(() => {
      if (cancelled || !instance.current) return
      try {
        // Reduced motion: charts appear without their entry and update animation.
        const still = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
        instance.current.setOption({ aria: { enabled: true, label: { description: label } }, ...build(readTokens(), width),
          ...(still ? { animation: false } : {}) }, true)
      } catch {
        setFailed(true)
      }
    }).catch(() => { if (!cancelled) setFailed(true) })
    return () => { cancelled = true }
  }, [build, label, theme, width])

  // Keyboard: the arrow keys move between marks and Enter or Space acts as a click on the mark.
  const [active, setActive] = useState<{ index: number; text: string } | null>(null)
  const marks = () => {
    const data = (instance.current as unknown as { getModel?: () => { getSeriesByIndex: (index: number) => { getData: () => MarkData } | undefined } } | null)
      ?.getModel?.().getSeriesByIndex(0)?.getData()
    return data && data.count() > 0 ? data : null
  }
  const clear = () => {
    instance.current?.dispatchAction({ type: 'downplay', seriesIndex: 0 })
    instance.current?.dispatchAction({ type: 'hideTip' })
    setActive(null)
  }
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const chart = instance.current
    const data = marks()
    if (!chart || !data) return
    const count = data.count()
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key]
    if (step !== undefined) {
      event.preventDefault()
      const index = active === null ? (step > 0 ? 0 : count - 1) : (active.index + step + count) % count
      chart.dispatchAction({ type: 'downplay', seriesIndex: 0 })
      chart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: index })
      chart.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: index })
      setActive({ index, text: `${data.getName(index) || `Item ${index + 1}`}, ${index + 1} of ${count}` })
    } else if ((event.key === 'Enter' || event.key === ' ') && active) {
      event.preventDefault()
      const at = markCenter(data.getItemLayout(active.index))
      select.current?.({ seriesIndex: 0, dataIndex: active.index, name: data.getName(active.index), ...at,
        additive: event.shiftKey || event.ctrlKey || event.metaKey })
    } else if (event.key === 'Escape' && active) {
      clear()
    }
  }

  if (failed) return <p role='alert' className='p-3 text-sm text-muted-foreground'>The chart could not be drawn.</p>
  const drawing = <div ref={container} role='img' aria-label={label} className={onSelect ? 'size-full min-h-0 min-w-0' : className ?? 'size-full min-h-0 min-w-0'} />
  if (!onSelect) return drawing
  return <div role='group' tabIndex={0} aria-label={`${label}. Use the arrow keys to move between marks and Enter to select one.`}
    onKeyDown={onKeyDown} onBlur={() => { if (active) clear() }}
    className={cn(className ?? 'size-full min-h-0 min-w-0', 'rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring')}>
    {drawing}
    <span aria-live='polite' className='sr-only'>{active?.text ?? ''}</span>
  </div>
}

type MarkData = { count: () => number; getName: (index: number) => string; getItemLayout: (index: number) => unknown }

/** Where a mark is drawn, so the action menu opens beside it as it does for a click. */
function markCenter(layout: unknown): { x?: number; y?: number } {
  if (Array.isArray(layout) && typeof layout[0] === 'number') return { x: layout[0], y: layout[1] as number }
  const box = layout as { x?: number; y?: number; width?: number; height?: number; cx?: number; cy?: number
    r?: number; r0?: number; startAngle?: number; endAngle?: number } | null
  if (box && typeof box.x === 'number' && typeof box.width === 'number') {
    return { x: box.x + box.width / 2, y: (box.y ?? 0) + (box.height ?? 0) / 2 }
  }
  if (box && typeof box.cx === 'number' && typeof box.cy === 'number') {
    const angle = ((box.startAngle ?? 0) + (box.endAngle ?? 0)) / 2
    const radius = ((box.r ?? 0) + (box.r0 ?? 0)) / 2
    return { x: box.cx + radius * Math.cos(angle), y: box.cy + radius * Math.sin(angle) }
  }
  return {}
}
