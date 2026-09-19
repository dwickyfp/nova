import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { Markdown } from './markdown'

describe('Markdown', () => {
  it('renders headings, emphasis and lists', async () => {
    const { getByRole, getByText } = await render(
      <Markdown>{'# Title\n\nSome **bold** text.\n\n- one\n- two'}</Markdown>
    )

    await expect.element(getByRole('heading', { name: 'Title' })).toBeInTheDocument()
    await expect.element(getByText('bold')).toBeInTheDocument()
    expect(document.querySelectorAll('li')).toHaveLength(2)
  })

  it('renders a GFM table', async () => {
    const { getByText } = await render(
      <Markdown>{'| name | value |\n| --- | --- |\n| rows | 42 |'}</Markdown>
    )

    await expect.element(getByText('name')).toBeInTheDocument()
    await expect.element(getByText('rows')).toBeInTheDocument()
    expect(document.querySelector('table')).not.toBeNull()
  })

  it('renders a fenced code block with highlighting', async () => {
    const { container } = await render(<Markdown>{'```sql\nSELECT * FROM t;\n```'}</Markdown>)
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    // highlight.js wraps keywords in spans with hljs-* classes.
    expect(container.querySelector('.hljs-keyword')).not.toBeNull()
  })

  it('gives a SQL block a Run button when a run context is supplied', async () => {
    const { getByRole } = await render(
      <Markdown runContext={{ database: 'analytics', schema: 'public', role: 'ACCOUNTADMIN' }}>
        {'```sql\nSELECT 1;\n```'}
      </Markdown>
    )
    await expect.element(getByRole('button', { name: 'Run statement' })).toBeInTheDocument()
    await expect.element(getByRole('button', { name: 'Copy code' })).toBeInTheDocument()
  })

  it('omits Run without a run context (bare renderer)', async () => {
    const { container, getByRole } = await render(
      <Markdown>{'```sql\nSELECT 1;\n```'}</Markdown>
    )
    expect(container.querySelector('[aria-label="Run statement"]')).toBeNull()
    await expect.element(getByRole('button', { name: 'Copy code' })).toBeInTheDocument()
  })

  it('offers no Run for a non-SQL fenced block', async () => {
    const { container } = await render(
      <Markdown>{'```json\n{"a": 1}\n```'}</Markdown>
    )
    expect(container.querySelector('[aria-label="Run statement"]')).toBeNull()
  })

  it('renders inline code without a block', async () => {
    const { container } = await render(<Markdown>{'Use `SELECT` here.'}</Markdown>)
    expect(container.querySelector('pre')).toBeNull()
    expect(container.querySelector('code')?.textContent).toBe('SELECT')
  })

  it('does not render raw HTML as markup', async () => {
    const { container } = await render(<Markdown>{'<img src=x onerror=alert(1)>'}</Markdown>)
    expect(container.querySelector('img')).toBeNull()
  })
})
