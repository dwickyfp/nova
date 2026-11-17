import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { getInstanceByDom, type ECharts } from 'echarts/core'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { NativeChart, type ValueSelection } from './native-chart'
import '@/styles/index.css'

// Release QA for rendered native charts (docs/reports/native-dataviz/QA_MATRIX.csv).

const DATASET = 'aaaaaaaa-0000-4000-8000-0000000000d1'
type Column = DatasetQueryResult['columns'][number]
type Row = DatasetQueryResult['rows'][number]
const REGION = { key: 'd0', name: 'region', logical_type: 'string', role: 'dimension', field_id: 'region' } as Column
const REVENUE = { key: 'm0', name: 'revenue', logical_type: 'decimal', role: 'measure', field_id: 'revenue', aggregation: 'sum' } as Column
const s = (value: string) => ({ type: 'string' as const, value })
const d = (value: string) => ({ type: 'decimal' as const, value })
const body = (columns: Column[], rows: Row[]) => ({ dataset_id: DATASET, revision: 1, truncated: false, columns, rows })

const definition = (visual: ChartDefinition['visual'], encoding: unknown, title = 'Revenue by region') =>
  ({ schema_version: 2, chart_id: 'cccccccc-0000-4000-8000-0000000000d1', title, dataset_id: DATASET, dataset_revision: 1, visual, encoding } as ChartDefinition)
const bar = definition('bar', { kind: 'cartesian', dimensions: [{ field_id: 'region' }], measures: [{ field_id: 'revenue', aggregation: 'sum' }] })

let replies: { status: number; body: unknown }[] = []
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input)
  if (!url.includes(`/viz/datasets/${DATASET}/query`)) throw new Error(`Unexpected request ${url}`)
  const reply = replies.length > 1 ? replies.shift()! : replies[0]
  return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { 'Content-Type': 'application/json' } })
})
const queries = () => fetchMock.mock.calls.length

function respond(...list: { status: number; body: unknown }[]) {
  replies = list
  fetchMock.mockClear()
  vi.stubGlobal('fetch', fetchMock)
}

async function show(chart: ChartDefinition, onSelectValue?: (selection: ValueSelection) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>
    <div style={{ width: 480, height: 320, display: 'flex' }}><NativeChart chart={chart} draft={false} onSelectValue={onSelectValue} /></div>
  </QueryClientProvider>)
}

/** The drawn ECharts instance, once its option has been set. */
async function drawn(container: HTMLElement): Promise<{ host: HTMLElement; chart: ECharts }> {
  let found: { host: HTMLElement; chart: ECharts } | null = null
  await expect.poll(() => {
    const host = container.querySelector<HTMLElement>('[role="img"]')
    const chart = host ? getInstanceByDom(host) : undefined
    found = host && chart && (chart.getOption() as { series?: unknown[] } | undefined)?.series?.length ? { host, chart } : null
    return found !== null
  }).toBe(true)
  // Let the entry animation settle so marks are where they will stay.
  await new Promise(resolve => setTimeout(resolve, 600))
  return found!
}

async function clickBar(host: HTMLElement, chart: ECharts, category: string, value: number) {
  const [x, y] = chart.convertToPixel({ seriesIndex: 0 }, [category, value / 2]) as number[]
  await userEvent.click(host.querySelector('canvas')!, { position: { x: Math.round(x), y: Math.round(y) } })
}

afterEach(async () => {
  await cleanup()
  vi.unstubAllGlobals()
  document.documentElement.classList.remove('dark')
  delete (window as { __ndvXss?: unknown }).__ndvXss
})

describe('NDV-FILTER-019 KPI with no data', () => {
  const kpi = definition('kpi', { kind: 'single_measure',
    measure: { field_id: 'revenue', aggregation: 'sum', format: { kind: 'number', decimals: 0, locale: 'en-US' } } }, 'Revenue')

  it('says there is no data, rather than showing a bare dash, for no rows and for a null total', async () => {
    for (const rows of [[], [[null]]] as Row[][]) {
      respond({ status: 200, body: body([REVENUE], rows) })
      const screen = await show(kpi)
      await expect.element(screen.getByText('No data for the current filters', { exact: true })).toBeVisible()
      await expect.element(screen.getByRole('status')).toHaveTextContent('No data for the current filters')
      expect(screen.getByTestId('kpi-value').element().querySelector('[aria-hidden="true"]')?.textContent).toBe('–')
      await cleanup()
    }
  })
})

describe('NDV-CHART-012 KPI delta', () => {
  const kpi = definition('kpi', {
    kind: 'single_measure',
    measure: { field_id: 'revenue', aggregation: 'sum', format: { kind: 'number', decimals: 0, locale: 'en-US' } },
    comparison: { field_id: 'revenue_prior', aggregation: 'sum', format: { kind: 'number', decimals: 0, locale: 'en-US' } },
  }, 'Revenue')
  const PRIOR = { ...REVENUE, key: 'm1', name: 'revenue_prior', field_id: 'revenue_prior' } as Column
  const kpiBody = (current: string | null, prior: string | null) =>
    body([REVENUE, PRIOR], [[current === null ? null : d(current), prior === null ? null : d(prior)]])

  it('shows the current value and the change against the prior period as a share of the prior', async () => {
    respond({ status: 200, body: kpiBody('120', '100') })
    const up = await show(kpi)
    await expect.element(up.getByTestId('kpi-value')).toHaveTextContent('120')
    await expect.element(up.getByText('+20.0%')).toBeVisible()
    await expect.element(up.getByText('vs sum of revenue prior')).toBeVisible()
    await cleanup()

    respond({ status: 200, body: kpiBody('80', '100') })
    const down = await show(kpi)
    await expect.element(down.getByTestId('kpi-value')).toHaveTextContent('80')
    await expect.element(down.getByText('-20.0%')).toBeVisible()
    await cleanup()

    // A negative prior period: the change is measured against its magnitude.
    respond({ status: 200, body: kpiBody('-50', '-100') })
    const recovered = await show(kpi)
    await expect.element(recovered.getByTestId('kpi-value')).toHaveTextContent('-50')
    await expect.element(recovered.getByText('+50.0%')).toBeVisible()
  })

  it('shows no percentage when the prior period is zero or missing', async () => {
    for (const prior of ['0', null]) {
      respond({ status: 200, body: kpiBody('120', prior) })
      const screen = await show(kpi)
      await expect.element(screen.getByTestId('kpi-value')).toHaveTextContent('120')
      expect(screen.container.textContent).not.toMatch(/%|Infinity|NaN/)
      expect(screen.container.textContent).not.toContain('vs ')
      await cleanup()
    }
  })
})

describe('NDV-CHART-013 unsafe text in a chart', () => {
  it('keeps markup in a category inert in the tooltip, the chart and its data table', async () => {
    const unsafe = '<img src=x onerror="window.__ndvXss=1">'
    respond({ status: 200, body: body([REGION, REVENUE], [[s(unsafe), d('400')], [s('Bandung'), d('600')]]) })
    const screen = await show(bar)
    const { host, chart } = await drawn(screen.container)
    chart.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: 0 })
    await expect.poll(() => host.textContent ?? '').toContain(unsafe)
    // The tooltip shows the text itself; no element was created from it.
    expect(document.querySelectorAll('img')).toHaveLength(0)
    expect(host.innerHTML).toContain('&lt;img src=x')
    await new Promise(resolve => setTimeout(resolve, 100))
    expect((window as { __ndvXss?: unknown }).__ndvXss).toBeUndefined()
    // The screen-reader table carries the same exact text.
    await expect.element(screen.getByRole('cell', { name: unsafe })).toBeInTheDocument()
  })
})

describe('NDV-CHART-014 theme switch', () => {
  it('redraws from the dark tokens and back without querying again or remounting', async () => {
    document.documentElement.classList.remove('dark')
    respond({ status: 200, body: body([REGION, REVENUE], [[s('Jakarta'), d('400')], [s('Bandung'), d('600')]]) })
    const screen = await show(bar)
    const { host, chart } = await drawn(screen.container)
    const canvas = host.querySelector('canvas')
    const colors = () => {
      const option = chart.getOption() as { textStyle: { color: string } | { color: string }[]; color: string[] }
      const text = Array.isArray(option.textStyle) ? option.textStyle[0].color : option.textStyle.color
      return { text, mark: option.color[0] }
    }
    const token = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim().toLowerCase()
    const light = { text: token('--foreground'), mark: token('--viz-mark') }
    expect(colors()).toEqual(light)

    document.documentElement.classList.add('dark')
    const dark = { text: token('--foreground'), mark: token('--viz-mark') }
    expect(dark.text).not.toBe(light.text)
    expect(dark.mark).not.toBe(light.mark)
    await expect.poll(colors).toEqual(dark)

    document.documentElement.classList.remove('dark')
    await expect.poll(colors).toEqual(light)
    expect(getInstanceByDom(host)).toBe(chart)
    expect(host.querySelector('canvas')).toBe(canvas)
    expect(queries()).toBe(1)
  })
})

const NO_CATEGORY = 'This chart shows totals only, so clicking it cannot filter other charts.'

describe('NDV-INTER-003 measure-only charts', () => {
  it('emits no selection when a KPI is clicked', async () => {
    const onSelect = vi.fn()
    respond({ status: 200, body: body([REVENUE], [[d('15257')]]) })
    const screen = await show(definition('kpi', { kind: 'single_measure', measure: { field_id: 'revenue', aggregation: 'sum' } }, 'Revenue'), onSelect)
    const value = screen.getByTestId('kpi-value')
    await expect.element(value).toHaveTextContent('15,257')
    await userEvent.click(value)
    await userEvent.click(screen.container.firstElementChild as HTMLElement)
    expect(onSelect).not.toHaveBeenCalled()
    expect(screen.container.querySelector('button, [role="button"], [tabindex]')).toBeNull()
    // Pointing at it says why it cannot filter.
    await expect.element(screen.getByTitle(NO_CATEGORY)).toBeInTheDocument()
  })

  it('emits no selection when a bar of a chart without a grouping is clicked', async () => {
    const onSelect = vi.fn()
    respond({ status: 200, body: body([REVENUE], [[d('400')]]) })
    const screen = await show(definition('bar', { kind: 'cartesian', dimensions: [], measures: [{ field_id: 'revenue', aggregation: 'sum' }] }, 'Revenue'), onSelect)
    const { host, chart } = await drawn(screen.container)
    const hits: number[] = []
    chart.on('click', params => { hits.push((params as { dataIndex: number }).dataIndex) })
    await clickBar(host, chart, '', 400)
    // The click reached the bar itself, and the chart still emitted nothing.
    await expect.poll(() => hits).toEqual([0])
    await new Promise(resolve => setTimeout(resolve, 200))
    expect(onSelect).not.toHaveBeenCalled()
    await expect.element(screen.getByTitle(NO_CATEGORY)).toBeInTheDocument()
  })
})

describe('NDV-INTER-004 selection from an aggregated mark', () => {
  it('selects only the dimension value of the clicked bar, never the measure', async () => {
    const onSelect = vi.fn()
    respond({ status: 200, body: body([REGION, REVENUE], [[s('Jakarta'), d('400')], [s('Bandung'), d('600')]]) })
    const screen = await show(bar, onSelect)
    const { host, chart } = await drawn(screen.container)
    await clickBar(host, chart, 'Bandung', 600)
    await expect.poll(() => onSelect.mock.calls.length).toBe(1)
    const selection = onSelect.mock.calls[0][0] as ValueSelection
    expect(selection).toMatchObject({ field_id: 'region', value: { type: 'string', value: 'Bandung' }, label: 'Bandung' })
    expect(Object.keys(selection).sort()).toEqual(['anchor', 'field_id', 'label', 'value'])
    expect(JSON.stringify(selection)).not.toMatch(/revenue|600/)
  })
})

describe('NDV-A11Y-010 empty, denied and failed charts', () => {
  const empty = { status: 200, body: body([REGION, REVENUE], []) }
  const denied = { status: 403, body: { detail: 'Source schema is unavailable or not permitted' } }
  const failed = { status: 500, body: { detail: 'Backend exploded at host 10.0.0.1' } }
  const ok = { status: 200, body: body([REGION, REVENUE], [[s('Jakarta'), d('400')]]) }

  it('gives each state its own descriptive message', async () => {
    respond(empty)
    const none = await show(bar)
    await expect.element(none.getByText('No rows match the current filters.')).toBeVisible()
    expect(none.container.querySelector('[role="alert"]')).toBeNull()
    expect(none.container.querySelector('button')).toBeNull()
    await cleanup()

    respond(denied)
    const refused = await show(bar)
    await expect.element(refused.getByRole('alert')).toHaveTextContent('This data is unavailable with your current role.')
    expect(refused.container.textContent).not.toContain('Source schema')
    await cleanup()

    respond(failed)
    const broken = await show(bar)
    await expect.element(broken.getByRole('alert')).toHaveTextContent('The chart could not be loaded.')
    expect(broken.container.textContent).not.toContain('10.0.0.1')
  })

  it('retries a failed query and then draws the chart', async () => {
    respond(failed, ok)
    const screen = await show(bar)
    await expect.element(screen.getByRole('alert')).toHaveTextContent('The chart could not be loaded.')
    expect(queries()).toBe(1)
    await screen.getByRole('button', { name: 'Retry' }).click()
    await expect.element(screen.getByRole('img', { name: 'Revenue by region: bar chart' })).toBeInTheDocument()
    expect(screen.container.querySelector('[role="alert"]')).toBeNull()
    expect(queries()).toBe(2)
  })
})
