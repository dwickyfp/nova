import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadEcharts } from './echart-host'

/*
 * NDV-OSS-004: native charts draw through the modular Apache ECharts core with
 * only the series the product uses registered, never the full ECharts entry.
 */

const SERIES: Record<string, Record<string, unknown>> = {
  bar: { type: 'bar', data: [1, 2] },
  line: { type: 'line', data: [1, 2] },
  pie: { type: 'pie', data: [{ name: 'a', value: 1 }] },
  scatter: { type: 'scatter', data: [[1, 2]] },
  heatmap: { type: 'heatmap', data: [[0, 0, 1]] },
  funnel: { type: 'funnel', data: [{ name: 'a', value: 1 }] },
  treemap: { type: 'treemap', data: [{ name: 'a', value: 1 }] },
  sankey: { type: 'sankey', data: [{ name: 'a' }, { name: 'b' }], links: [{ source: 'a', target: 'b', value: 1 }] },
  gauge: { type: 'gauge', data: [{ value: 1 }] },
  boxplot: { type: 'boxplot', data: [[1, 2, 3, 4, 5]] },
}
const CARTESIAN = new Set(['bar', 'line', 'scatter', 'heatmap', 'boxplot'])

const hosts: HTMLElement[] = []
afterEach(() => {
  for (const host of hosts.splice(0)) host.remove()
  vi.restoreAllMocks()
})

async function draw(series: Record<string, unknown>) {
  const core = await loadEcharts()
  const host = document.createElement('div')
  host.style.cssText = 'width:320px;height:200px'
  document.body.append(host)
  hosts.push(host)
  const chart = core.init(host, undefined, { renderer: 'canvas' })
  const type = String(series.type)
  chart.setOption({
    animation: false,
    ...(CARTESIAN.has(type) ? { xAxis: { type: 'category', data: ['x', 'y'] },
      yAxis: type === 'heatmap' ? { type: 'category', data: ['p', 'q'] } : { type: 'value' } } : {}),
    ...(type === 'heatmap' ? { visualMap: { min: 0, max: 1, show: false } } : {}),
    series: [series],
  })
  return { chart, host }
}

describe('registered ECharts modules', () => {
  it.each(Object.keys(SERIES))('draws a %s series', async (type) => {
    const { chart, host } = await draw(SERIES[type])
    const model = (chart as unknown as { getModel: () => { getSeriesByType: (name: string) => unknown[] } }).getModel()
    expect(model.getSeriesByType(type)).toHaveLength(1)
    expect(host.querySelector('canvas')).not.toBeNull()
    chart.dispose()
  })

  it('does not carry series the product does not register', async () => {
    // ECharts reports an unknown series type on the console and draws nothing.
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const { chart } = await draw({ type: 'radar', data: [{ value: [1, 2, 3] }] })
    const model = (chart as unknown as { getModel: () => { getSeriesByType: (name: string) => unknown[] } }).getModel()
    expect(model.getSeriesByType('radar')).toHaveLength(0)
    chart.dispose()
  })
})
