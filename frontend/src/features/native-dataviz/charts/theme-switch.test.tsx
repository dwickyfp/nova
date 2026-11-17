import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { getInstanceByDom } from 'echarts/core'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from 'vitest-browser-react'
import type { ChartDefinition } from '../contracts'
import { NativeChart } from './native-chart'
import { readTokens } from './echart-host'
import '@/styles/index.css'

/* NDV-A11Y-004: a drawn chart follows a theme switch and its text stays readable. */

const DATASET = 'dddddddd-0000-4000-8000-000000000001'
const bar = {
  schema_version: 2, chart_id: 'cccccccc-0000-4000-8000-000000000001', title: 'Revenue by region',
  dataset_id: DATASET, dataset_revision: 1, visual: 'bar',
  encoding: { kind: 'cartesian', dimensions: [{ field_id: 'region' }], measures: [{ field_id: 'revenue', aggregation: 'sum' }] },
} as unknown as ChartDefinition

/** Paint the colour and read the pixel back, so any CSS colour syntax becomes sRGB. */
function rgb(color: string): [number, number, number] {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const context = canvas.getContext('2d', { willReadFrequently: true })!
  context.fillStyle = color
  context.fillRect(0, 0, 1, 1)
  const [r, g, b] = context.getImageData(0, 0, 1, 1).data
  return [r, g, b]
}

function contrast(a: string, b: string) {
  const luminance = (color: string) => {
    const [r, g, b] = rgb(color).map(value => {
      const channel = value / 255
      return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (high + 0.05) / (low + 0.05)
}

afterEach(async () => {
  await cleanup()
  document.documentElement.classList.remove('dark')
  vi.unstubAllGlobals()
})

describe('NDV-A11Y-004 theme switch with a chart on screen', () => {
  it('re-reads colours from the theme and keeps axis text at AA contrast in light and dark', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ dataset_id: DATASET, revision: 1, truncated: false,
      columns: [
        { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' },
        { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' }],
      rows: [[{ type: 'string', value: 'East' }, { type: 'decimal', value: '10' }]] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const screen = await render(<QueryClientProvider client={client}>
      <div style={{ width: 480, height: 320, display: 'flex' }}><NativeChart chart={bar} draft={false} /></div>
    </QueryClientProvider>)
    const instance = await vi.waitFor(() => {
      const host = screen.container.querySelector<HTMLElement>('[role="img"]')
      const chart = host && getInstanceByDom(host)
      if (!chart || !(chart.getOption() as { xAxis?: unknown[] }).xAxis?.length) throw new Error('not drawn')
      return chart
    })
    const axisText = () => ((instance.getOption() as { xAxis: { axisLabel: { color: string } }[] }).xAxis[0].axisLabel.color)

    const light = readTokens()
    expect(axisText()).toBe(light.muted)
    expect(contrast(light.muted, light.surface)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(light.text, light.surface)).toBeGreaterThanOrEqual(4.5)

    document.documentElement.classList.add('dark')
    const dark = readTokens()
    expect(dark.muted).not.toBe(light.muted)
    // The same instance redraws with the dark tokens; no reload or remount.
    await vi.waitFor(() => expect(axisText()).toBe(dark.muted))
    expect(contrast(dark.muted, dark.surface)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(dark.text, dark.surface)).toBeGreaterThanOrEqual(4.5)
    expect(screen.container.querySelector<HTMLElement>('[role="img"]') && getInstanceByDom(screen.container.querySelector<HTMLElement>('[role="img"]')!)).toBe(instance)
  })
})
