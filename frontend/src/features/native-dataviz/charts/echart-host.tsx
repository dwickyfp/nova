import { useEffect, useRef, useState } from 'react'
import type { ECharts, EChartsCoreOption } from 'echarts/core'
import { readToken } from '@/lib/read-token'

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

export type MarkSelection = { seriesIndex: number; dataIndex: number; name: string }

function readTokens(): VizTokens {
  // theme.css owns every color; tokens are always defined where charts render.
  const token = (name: string) => readToken(name, '')
  return {
    series: [4, 2, 3, 1, 5, 6, 8, 9, 7].map(index => token(`--chart-${index}`)),
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

function loadEcharts() {
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
  build: (tokens: VizTokens) => EChartsCoreOption
  label: string
  onSelect?: (selection: MarkSelection) => void
  className?: string
}) {
  const container = useRef<HTMLDivElement | null>(null)
  const instance = useRef<ECharts | null>(null)
  const select = useRef(onSelect)
  const [failed, setFailed] = useState(false)
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
        const event = params as { seriesIndex?: number; dataIndex?: number; name?: string }
        if (typeof event.dataIndex === 'number') {
          select.current?.({ seriesIndex: event.seriesIndex ?? 0, dataIndex: event.dataIndex, name: String(event.name ?? '') })
        }
      })
      observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => chart.resize())
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
        instance.current.setOption({ aria: { enabled: true, label: { description: label } }, ...build(readTokens()) }, true)
      } catch {
        setFailed(true)
      }
    }).catch(() => { if (!cancelled) setFailed(true) })
    return () => { cancelled = true }
  }, [build, label, theme])

  if (failed) return <p role='alert' className='p-3 text-sm text-muted-foreground'>The chart could not be drawn.</p>
  return <div ref={container} role='img' aria-label={label} className={className ?? 'size-full min-h-0 min-w-0'} />
}
