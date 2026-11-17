import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { useAuthStore } from '@/stores/auth-store'
import type { Parameter } from './contracts'
import { DatasetPreviewPane } from './dataset-preview'
import { initialParameterDrafts, parameterScalar, resolveParameterInputs } from './parameter-values'
import '@/styles/index.css'

const datasetId = 'aaaaaaaa-0000-4000-8000-000000000001'
const fieldId = 'aaaaaaaa-0000-4000-8000-000000000002'
const fetchMock = vi.fn()
const clients: QueryClient[] = []
const json = (body: unknown) => new Response(JSON.stringify(body), {
  status: 200, headers: { 'Content-Type': 'application/json' },
})
const result = {
  dataset_id: datasetId, revision: 2, offset: 0, has_more: false, next_offset: null,
  fields: [{ field_id: fieldId, source_column: 'city', name: 'City', logical_type: 'string',
    physical_type: 'VARCHAR(64)', semantic_role: 'dimension', nullable: false }],
  rows: [[{ type: 'string', value: 'Jakarta' }]],
}
const parameter = (value: Partial<Parameter> & Pick<Parameter, 'parameter_id' | 'type'>) => value as Parameter

async function mount(parameters: Parameter[]) {
  const client = new QueryClient()
  clients.push(client)
  return render(<QueryClientProvider client={client}>
    <div style={{ height: 640, width: '100%', display: 'flex', minWidth: 0 }}>
      <DatasetPreviewPane datasetId={datasetId} revision={2} draft title='Sales' parameters={parameters} />
    </div>
  </QueryClientProvider>)
}
const body = (call = 0) => JSON.parse(fetchMock.mock.calls[call][1].body)

beforeEach(() => {
  useAuthStore.getState().auth.setAccessToken('parameter-test-token')
  useAuthStore.getState().auth.setUser({ username: 'alice', roles: ['ANALYST'], activeRole: 'ANALYST' })
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockImplementation(async () => json(result))
})
afterEach(async () => {
  await cleanup()
  for (const client of clients.splice(0)) client.clear()
  useAuthStore.getState().auth.reset()
  vi.unstubAllGlobals()
  await page.viewport(1280, 720)
})

describe('parameter value conversion', () => {
  it('keeps typed text exact and rejects ambiguous Boolean text', () => {
    expect(parameterScalar('decimal', '12345678901234567890.123456789')).toEqual({ type: 'decimal', value: '12345678901234567890.123456789' })
    expect(parameterScalar('enum', 'North')).toEqual({ type: 'string', value: 'North' })
    expect(parameterScalar('boolean', 'false')).toEqual({ type: 'boolean', value: false })
    expect(() => parameterScalar('boolean', 'yes')).toThrow('Choose a Boolean value.')
  })

  it('omits defaulted values, sends NULL only when declared nullable and refuses sensitive previews', () => {
    const declared = [
      parameter({ parameter_id: 'region', type: 'string', defaults: [{ type: 'string', value: 'North' }] }),
      parameter({ parameter_id: 'limit_day', type: 'date', nullable: true }),
    ]
    const drafts = initialParameterDrafts(declared)
    expect(drafts.map(draft => draft.mode)).toEqual(['default', 'value'])
    expect(resolveParameterInputs(declared, [drafts[0], { mode: 'null', values: [''] }]))
      .toEqual([{ parameter_id: 'limit_day', values: [null] }])
    expect(() => resolveParameterInputs([parameter({ parameter_id: 'day', type: 'date' })], [{ mode: 'null', values: [''] }]))
      .toThrow('Enter a valid date value for day.')
    expect(() => resolveParameterInputs([parameter({ parameter_id: 'day', type: 'date' })], [{ mode: 'default', values: [''] }]))
      .toThrow('Enter a value for day.')
    expect(() => resolveParameterInputs([parameter({ parameter_id: 'secret', type: 'string', sensitive: true })], [{ mode: 'value', values: ['x'] }]))
      .toThrow('Preview is unavailable for sensitive parameters.')
  })
})

describe('preview parameter controls', () => {
  it('sends a typed required value only after an explicit preview', async () => {
    const screen = await mount([parameter({ parameter_id: 'min_amount', type: 'integer' })])
    await expect.element(screen.getByRole('group', { name: 'min_amount' })).toBeVisible()
    await screen.getByRole('textbox', { name: 'min_amount value' }).fill('42')
    expect(fetchMock).not.toHaveBeenCalled()
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByRole('gridcell', { name: 'Jakarta' })).toBeVisible()
    expect(body()).toEqual({ revision: 2, draft: true, offset: 0, limit: 100,
      parameters: [{ parameter_id: 'min_amount', values: [{ type: 'integer', value: '42' }] }] })
  })

  it('shows a field-specific error and does not query when a required value is invalid', async () => {
    const screen = await mount([parameter({ parameter_id: 'min_amount', type: 'integer' })])
    await screen.getByRole('textbox', { name: 'min_amount value' }).fill('4.5')
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('Enter a valid integer value for min_amount.')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('adds and removes list values and resets the preview when inputs change', async () => {
    const screen = await mount([parameter({ parameter_id: 'city', type: 'string', multiple: true })])
    await screen.getByRole('textbox', { name: 'city value 1' }).fill('Jakarta')
    await expect.element(screen.getByRole('button', { name: 'Remove city value 1' })).toBeDisabled()
    await screen.getByRole('button', { name: 'Add city value' }).click()
    await screen.getByRole('textbox', { name: 'city value 2' }).fill('Bandung')
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByRole('gridcell', { name: 'Jakarta' })).toBeVisible()
    expect(body().parameters).toEqual([{ parameter_id: 'city', values: [
      { type: 'string', value: 'Jakarta' }, { type: 'string', value: 'Bandung' }] }])
    await screen.getByRole('button', { name: 'Remove city value 2' }).click()
    await expect.element(screen.getByText('Run a preview to read this dataset with your current role.')).toBeVisible()
    await expect.element(screen.getByRole('button', { name: 'Run preview' })).toBeVisible()
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByRole('gridcell', { name: 'Jakarta' })).toBeVisible()
    expect(body(1).parameters).toEqual([{ parameter_id: 'city', values: [{ type: 'string', value: 'Jakarta' }] }])
  })

  it('uses dataset defaults without sending a value and offers NULL only for nullable parameters', async () => {
    const screen = await mount([
      parameter({ parameter_id: 'region', type: 'string', defaults: [{ type: 'string', value: 'North' }] }),
      parameter({ parameter_id: 'day', type: 'date', nullable: true }),
    ])
    await screen.getByRole('combobox', { name: 'day input mode' }).click()
    await screen.getByRole('option', { name: 'NULL' }).click()
    await screen.getByRole('combobox', { name: 'region input mode' }).click()
    await expect.element(screen.getByRole('option', { name: 'Use dataset default' })).toBeVisible()
    await expect.element(screen.getByRole('option', { name: 'NULL' })).not.toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByRole('gridcell', { name: 'Jakarta' })).toBeVisible()
    expect(body().parameters).toEqual([{ parameter_id: 'day', values: [null] }])
  })

  it('disables preview for a sensitive parameter without rendering a value input', async () => {
    const screen = await mount([parameter({ parameter_id: 'token', type: 'string', sensitive: true })])
    await expect.element(screen.getByText('Preview is unavailable for sensitive parameters.')).toBeVisible()
    await expect.element(screen.getByRole('textbox', { name: 'token value' })).not.toBeInTheDocument()
    await expect.element(screen.getByRole('button', { name: 'Run preview' })).toBeDisabled()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('keeps Boolean and allowed values to explicit choices at phone width', async () => {
    await page.viewport(360, 720)
    const screen = await mount([
      parameter({ parameter_id: 'active', type: 'boolean' }),
      parameter({ parameter_id: 'tier', type: 'enum', allowed: [{ type: 'string', value: 'Gold' }, { type: 'string', value: '' }] }),
    ])
    await screen.getByRole('combobox', { name: 'active value' }).click()
    await screen.getByRole('option', { name: 'true' }).click()
    await screen.getByRole('combobox', { name: 'tier value' }).click()
    await expect.element(screen.getByRole('option', { name: 'Empty text' })).toBeVisible()
    await screen.getByRole('option', { name: 'Gold' }).click()
    const region = screen.container.querySelector('section[aria-label="Sales preview"]')!
    expect(region.scrollWidth).toBeLessThanOrEqual(region.clientWidth)
    await screen.getByRole('button', { name: 'Run preview' }).click()
    await expect.element(screen.getByRole('gridcell', { name: 'Jakarta' })).toBeVisible()
    expect(body().parameters).toEqual([
      { parameter_id: 'active', values: [{ type: 'boolean', value: true }] },
      { parameter_id: 'tier', values: [{ type: 'string', value: 'Gold' }] },
    ])
  })
})
