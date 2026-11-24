import type * as MapLibreGL from 'maplibre-gl'
import { expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { ThemeProvider } from '@/context/theme-provider'
import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { NativeMap } from './native-map'
import '@/styles/index.css'

const chart = {
  schema_version: 2, chart_id: 'c', title: 'Cities', dataset_id: 'd', dataset_revision: 1, visual: 'map',
  encoding: { kind: 'map', latitude: 'lat', longitude: 'lng' },
} as unknown as ChartDefinition
const column = (key: string, name: string) =>
  ({ key, name, logical_type: 'float', role: 'dimension', field_id: name }) as DatasetQueryResult['columns'][number]
const f = (value: number) => ({ type: 'float' as const, value })
const result: DatasetQueryResult = {
  dataset_id: 'd', revision: 1, truncated: false,
  columns: [column('d0', 'lat'), column('d1', 'lng')],
  rows: [[f(-6.2), f(106.8)], [f(-7.25), f(112.75)]],
}

it('brings every point back into view from the in-map Show all locations button', async () => {
  const lib = await import('maplibre-gl')
  let map: MapLibreGL.Map | null = null
  // Capture the map the chart creates: its first fit is on its own data.
  const fit = vi.spyOn(lib.Map.prototype, 'fitBounds')
  const screen = await render(
    <ThemeProvider>
      <div style={{ width: 640, height: 360, display: 'flex' }}>
        <NativeMap chart={chart} result={result} label='Cities' />
      </div>
    </ThemeProvider>,
  )
  const button = screen.getByRole('button', { name: 'Show all locations' })
  await expect.element(button).toBeVisible()
  expect(screen.getByLabelText('Map viewport').element().contains(button.element())).toBe(true)
  await vi.waitFor(() => expect(fit).toHaveBeenCalled(), { timeout: 15000 })
  map = fit.mock.contexts[0] as MapLibreGL.Map
  fit.mockRestore()
  const both = () => {
    const view = map!.getBounds()
    return view.contains([106.8, -6.2]) && view.contains([112.75, -7.25])
  }
  await vi.waitFor(() => expect(both()).toBe(true), { timeout: 5000 })
  map!.jumpTo({ center: [100, 10], zoom: 9 }, { originalEvent: new MouseEvent('mousemove') })
  expect(both()).toBe(false)
  await button.click()
  await vi.waitFor(() => expect(both()).toBe(true), { timeout: 5000 })
})
