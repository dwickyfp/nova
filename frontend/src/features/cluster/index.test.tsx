import { afterEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { render } from 'vitest-browser-react'
import { RuntimeHealthPanel } from './index'
import type { RuntimeHealthResponse } from './api'

const healthy: RuntimeHealthResponse = {
  checked_at: '2026-09-21T00:00:00Z',
  redis: { status: 'healthy', message: 'Redis responded to PING.' },
  scheduler: {
    status: 'healthy',
    message: 'An active scheduler holds the leader lease.',
  },
  worker: {
    status: 'healthy',
    message: '2 active workers reporting heartbeats.',
    instances: 2,
  },
}

afterEach(async () => {
  await page.viewport(1280, 800)
})

describe('RuntimeHealthPanel', () => {
  it('shows each live service and the healthy summary', async () => {
    const { getByText } = await render(
      <RuntimeHealthPanel
        data={healthy}
        isLoading={false}
        isError={false}
        onRetry={() => {}}
      />,
    )

    await expect.element(getByText('Redis', { exact: true })).toBeInTheDocument()
    await expect.element(getByText('Nova scheduler')).toBeInTheDocument()
    await expect.element(getByText('Nova worker')).toBeInTheDocument()
    await expect.element(getByText('All healthy')).toBeInTheDocument()
  })

  it('makes unhealthy state prominent without inventing a count', async () => {
    const data: RuntimeHealthResponse = {
      ...healthy,
      scheduler: {
        status: 'unhealthy',
        message: 'No scheduler currently holds the leader lease.',
      },
    }
    const { getByText } = await render(
      <RuntimeHealthPanel
        data={data}
        isLoading={false}
        isError={false}
        onRetry={() => {}}
      />,
    )

    await expect
      .element(getByText('1 service needs attention'))
      .toBeInTheDocument()
    await expect.element(getByText('Unhealthy')).toBeInTheDocument()
  })

  it('shows a working retry action when the endpoint fails', async () => {
    const onRetry = vi.fn()
    const { getByRole, getByText } = await render(
      <RuntimeHealthPanel
        data={undefined}
        isLoading={false}
        isError
        onRetry={onRetry}
      />,
    )

    await expect
      .element(getByText('Could not check Nova services'))
      .toBeInTheDocument()
    await getByRole('button', { name: 'Retry' }).click()
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('stays inside a narrow viewport', async () => {
    await page.viewport(375, 800)
    const { container } = await render(
      <RuntimeHealthPanel
        data={healthy}
        isLoading={false}
        isError={false}
        onRetry={() => {}}
      />,
    )

    expect(container.scrollWidth).toBeLessThanOrEqual(container.clientWidth)
  })
})
