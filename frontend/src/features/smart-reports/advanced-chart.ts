import type { Widget, WidgetResult } from './contracts'

export function advancedChart(widget: Widget, result: WidgetResult, colors: string[], text: string) {
  const index = (field: string) => result.columns.findIndex(c => c === field || c === field.replace(/\./g, '__'))
  const x = index(widget.encoding.x ?? widget.query?.dimensions[0] ?? '')
  const metric = index(widget.encoding.value ?? widget.query?.metrics[0] ?? '')
  if (widget.kind === 'heatmap') {
    const y = index(widget.encoding.series ?? '')
    const xs = [...new Set(result.rows.map(r => JSON.stringify(r[x])))], ys = [...new Set(result.rows.map(r => JSON.stringify(r[y])))]
    const values = result.rows.filter(r => r[metric] != null).map(r => [xs.indexOf(JSON.stringify(r[x])), ys.indexOf(JSON.stringify(r[y])), Number(r[metric])])
    return { legend: { show: false }, tooltip: { trigger: 'item', renderMode: 'richText' },
      xAxis: { type: 'category', data: xs.map(v => String(JSON.parse(v) ?? '(Blank)')), axisLabel: { color: text } },
      yAxis: { type: 'category', data: ys.map(v => String(JSON.parse(v) ?? '(Blank)')), axisLabel: { color: text } },
      visualMap: { min: Math.min(0, ...values.map(v => v[2])), max: Math.max(1, ...values.map(v => v[2])), calculable: true, orient: 'horizontal', bottom: 0, textStyle: { color: text }, inRange: { color: colors.slice(0, 2) } },
      series: [{ type: 'heatmap', data: values }] }
  }
  if (widget.kind === 'waterfall') {
    let running = 0
    const data = result.rows.map(row => {
      const start = running
      running += Number(row[metric] ?? 0)
      return [String(row[x] ?? '(Blank)'), start, running]
    })
    return { legend: { show: false }, xAxis: { type: 'category', data: data.map(row => row[0]), axisLabel: { color: text } },
      series: [{ type: 'custom', encode: { x: 0, y: [1, 2] }, data,
        renderItem: (_params: unknown, api: { value: (i: number) => number; coord: (value: number[]) => number[]; size: (value: number[]) => number[]; style: () => Record<string, unknown> }) => {
          const start = api.coord([api.value(0), api.value(1)]), end = api.coord([api.value(0), api.value(2)])
          const width = api.size([1, 0])[0] * 0.65
          return { type: 'rect', shape: { x: start[0] - width / 2, y: Math.min(start[1], end[1]), width, height: Math.max(1, Math.abs(end[1] - start[1])) }, style: { ...api.style(), fill: api.value(2) >= api.value(1) ? colors[0] : colors[1] } }
        } }] }
  }
  return {}
}
