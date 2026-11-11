import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { DocsMarkdown } from './docs-markdown'
import { parseFrontMatter } from './content'
import fixture from './fixtures/render-check.md?raw'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}))

afterEach(() => vi.restoreAllMocks())

describe('documentation renderer', () => {
  it('renders section anchors, callouts, tables and copyable code', async () => {
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    const screen = await render(<DocsMarkdown>{parseFrontMatter(fixture).body}</DocsMarkdown>)
    await expect.element(screen.getByRole('heading', { name: 'First section' })).toHaveAttribute('id', 'first-section')
    await expect.element(screen.getByRole('complementary', { name: 'Note' })).toBeVisible()
    await expect.element(screen.getByRole('complementary', { name: 'Warning' })).toBeVisible()
    await expect.element(screen.getByRole('complementary', { name: 'Tip' })).toBeVisible()
    await expect.element(screen.getByRole('columnheader', { name: 'Column' })).toBeVisible()
    await screen.getByRole('button', { name: 'Copy code' }).first().click()
    expect(writeText).toHaveBeenCalledWith('SELECT region, SUM(amount) AS total\nFROM DEMO_SALES.orders\nGROUP BY region;')
    await expect.element(screen.getByRole('button', { name: 'Copied' })).toBeVisible()
  })

  it('keeps source text inert inside highlighted code', async () => {
    const screen = await render(<DocsMarkdown>{'```sql\nSELECT \'<img src=x onerror=alert(1)>\';\n```'}</DocsMarkdown>)
    expect(screen.container.querySelector('img')).toBeNull()
    expect(screen.container.querySelector('code')?.textContent).toContain('<img src=x onerror=alert(1)>')
  })
})
