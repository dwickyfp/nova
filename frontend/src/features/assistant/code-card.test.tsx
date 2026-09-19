import { afterEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { render } from 'vitest-browser-react'
import { CodeCard } from './code-card'

function renderCard(props: Partial<Parameters<typeof CodeCard>[0]> = {}) {
  return render(
    <CodeCard
      code='CREATE TABLE t (id BIGINT);'
      language='sql'
      highlighted={null}
      runnable
      runContext={{ database: 'analytics', schema: 'public', role: 'ACCOUNTADMIN' }}
      {...props}
    />
  )
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('CodeCard', () => {
  it('starts with a neutral status badge and offers Run and Copy', async () => {
    const { getByRole, getByText } = await renderCard()
    await expect.element(getByText('Not run')).toBeInTheDocument()
    await expect.element(getByRole('button', { name: 'Run statement' })).toBeInTheDocument()
    await expect.element(getByRole('button', { name: 'Copy code' })).toBeInTheDocument()
  })

  it('hides Run for a non-SQL block', async () => {
    const { container, getByRole } = await renderCard({ language: 'json', runnable: false })
    expect(container.querySelector('[aria-label="Run statement"]')).toBeNull()
    await expect.element(getByRole('button', { name: 'Copy code' })).toBeInTheDocument()
  })

  it('copies the code to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    // The browser harness has no clipboard by default; stub it. `clipboard` is a
    // getter on Navigator, so it must be redefined rather than assigned.
    vi.spyOn(navigator, 'clipboard', 'get').mockReturnValue({
      writeText,
    } as unknown as Clipboard)
    const { getByRole } = await renderCard()

    await getByRole('button', { name: 'Copy code' }).click()
    expect(writeText).toHaveBeenCalledWith('CREATE TABLE t (id BIGINT);')
  })

  it('runs the statement on click and turns the badge green', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([
          { success: true, row_count: 0, affected_rows: 1, elapsed_ms: 12, warnings: [] },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    )
    const { getByRole, getByText } = await renderCard()

    await getByRole('button', { name: 'Run statement' }).click()

    await expect.element(getByText('Success')).toBeInTheDocument()
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)
    expect(body.database).toBe('analytics')
    expect(body.role).toBe('ACCOUNTADMIN')
  })

  it('turns the badge red and shows the error when the run fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([
          { success: false, error: 'Table already exists', row_count: 0, elapsed_ms: 5, warnings: [] },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    )
    const { getByRole, getByText } = await renderCard()

    await getByRole('button', { name: 'Run statement' }).click()

    await expect.element(getByText('Failed')).toBeInTheDocument()
    await expect.element(getByText('Table already exists')).toBeInTheDocument()
  })

  it('runs without a native confirmation prompt', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([
          { success: true, row_count: 0, affected_rows: 1, elapsed_ms: 12, warnings: [] },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    )
    const { getByRole, getByText } = await renderCard()

    await getByRole('button', { name: 'Run statement' }).click()

    expect(confirm).not.toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await expect.element(getByText('Success')).toBeInTheDocument()
    void page
  })
})
