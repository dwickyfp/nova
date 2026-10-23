import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart, ScatterChart, HeatmapChart, FunnelChart, CustomChart } from 'echarts/charts'
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { Widget, WidgetResult, Scalar } from './contracts'
import { chartData } from './chart-data'
import { advancedChart } from './advanced-chart'

echarts.use([BarChart, LineChart, PieChart, ScatterChart, HeatmapChart, FunnelChart, CustomChart, AriaComponent, GridComponent, LegendComponent, TooltipComponent, VisualMapComponent, CanvasRenderer])

export function ReportChart({ widget, result, onSelect }: { widget: Widget; result: WidgetResult; onSelect?: (value: Scalar) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const node = ref.current
    if (!node) return
    const chart = echarts.init(node)
    const data = chartData(widget, result)
    const paint = () => {
      const style = getComputedStyle(node)
      const colors = [1, 2, 3, 4, 5].map(n => style.getPropertyValue(`--chart-${n}`).trim())
      const text = style.getPropertyValue('--foreground').trim()
      const labels = data.categories.map(value => String(value ?? '—'))
      const pie = widget.kind === 'donut' || widget.kind === 'funnel'
      chart.setOption({
        animation: false, color: colors, textStyle: { color: text, fontFamily: style.fontFamily },
        aria: { enabled: true, decal: { show: true } },
        tooltip: { trigger: pie ? 'item' : 'axis', renderMode: 'richText' },
        legend: { show: data.series.length > 1 || pie, bottom: 0, textStyle: { color: text } },
        grid: { left: 12, top: 24, right: 16, bottom: 48, containLabel: true },
        ...(!pie ? { xAxis: { type: widget.kind === 'scatter' ? 'value' : 'category', data: labels, axisLabel: { color: text, hideOverlap: true } }, yAxis: { type: 'value', axisLabel: { color: text } } } : {}),
        series: pie ? [{ type: widget.kind === 'funnel' ? 'funnel' : 'pie', radius: widget.kind === 'donut' ? ['45%', '70%'] : undefined, data: labels.map((name, i) => ({ name, value: data.series[0]?.values[i] })) }] : data.series.map((series, i) => ({
          name: series.name, type: widget.kind === 'scatter' ? 'scatter' : ['line', 'area'].includes(widget.kind) || widget.kind === 'combo' && i > 0 ? 'line' : 'bar',
          data: widget.kind === 'scatter' ? series.points : series.values,
          stack: widget.kind === 'stacked_bar' ? 'total' : undefined,
          areaStyle: widget.kind === 'area' ? {} : undefined, connectNulls: false,
        })),
        ...advancedChart(widget, result, colors, text),
      }, true)
      chart.resize()
    }
    paint()
    const resize = new ResizeObserver(() => chart.resize())
    resize.observe(node)
    const theme = new MutationObserver(paint)
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] })
    chart.on('click', params => {
      const category = widget.kind === 'heatmap' && Array.isArray(params.value) ? Number(params.value[0]) : params.dataIndex
      if (typeof category === 'number' && onSelect && widget.kind !== 'scatter') onSelect(data.categories[category] ?? null)
    })
    return () => { resize.disconnect(); theme.disconnect(); chart.dispose() }
  }, [widget, result, onSelect])
  return <div ref={ref} className="h-72 min-w-0 w-full" role="img" aria-label={`${widget.title}. Exact values are available in the data table.`} />
}
