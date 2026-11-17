import { readLightToken } from '@/lib/read-token'
import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { buildChartOption } from './chart-options'
import { loadEcharts, readTokens } from './echart-host'

/**
 * A chart drawn for paper: light theme on white, without animation, at twice
 * the pixel density, from the same definition and governed result as on screen.
 */
export async function printImage(chart: ChartDefinition, result: DatasetQueryResult, width: number, height: number,
  remainder: number | null = null): Promise<string> {
  const core = await loadEcharts()
  const host = document.createElement('div')
  host.setAttribute('aria-hidden', 'true')
  host.style.cssText = `position:fixed;left:-10000px;top:0;width:${width}px;height:${height}px`
  document.body.append(host)
  const instance = core.init(host, undefined, { renderer: 'canvas', width, height })
  try {
    instance.setOption({ ...buildChartOption(chart, result, readTokens('light'), null, width, remainder), animation: false })
    return instance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: readLightToken('--card', '#ffffff') })
  } finally {
    instance.dispose()
    host.remove()
  }
}
