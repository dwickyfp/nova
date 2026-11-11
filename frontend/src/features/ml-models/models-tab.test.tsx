import { expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { ModelsTab } from './models-tab'

vi.mock('@/lib/api-client', () => ({
  api: {
    get: vi.fn().mockResolvedValue({ models: [
      { model_id: 'a', model_name: 'Customer groups', model_type: 'clustering',
        latest_status: 'READY', latest_version: 1 },
      { model_id: 'b', model_name: 'Unusual orders', model_type: 'anomaly_detection',
        latest_status: 'FAILED', latest_version: 1 },
      { model_id: 'c', model_name: 'Next month', model_type: 'forecast',
        latest_status: 'ready', latest_version: 1 },
    ], count: 3 }),
  },
}))

it('counts engine READY statuses and preserves model types from SQL training', async () => {
  const screen = await render(<ModelsTab />)
  await expect.element(screen.getByText('2 of 3', { exact: true })).toBeVisible()
  await expect.element(screen.getByRole('cell', { name: 'Clustering', exact: true })).toBeVisible()
  await expect.element(screen.getByRole('cell', { name: 'Anomaly detection', exact: true })).toBeVisible()
  await expect.element(screen.getByRole('cell', { name: 'Forecasting', exact: true })).toBeVisible()
  await expect.element(screen.getByRole('cell', { name: 'Failed', exact: true })).toBeVisible()
})
