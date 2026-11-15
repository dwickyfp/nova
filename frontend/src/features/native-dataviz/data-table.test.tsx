import { afterEach, describe, expect, it } from 'vitest'
import { page, userEvent } from 'vitest/browser'
import { render } from 'vitest-browser-react'
import { DataTable } from './data-table'
import type { DatasetField, DatasetPreview } from './contracts'
import '@/styles/index.css'

const field = (name: string, type: DatasetField['logical_type'] = 'string'): DatasetField => ({
  field_id: name, name, source_column: name, logical_type: type, physical_type: 'VARCHAR',
  semantic_role: 'dimension', nullable: true,
})

function frame(fields: DatasetField[], rows: DatasetPreview['rows'], contextKey = 'dataset:1:epoch:1') {
  return <div style={{ height: 300, width: '100%', display: 'flex', minWidth: 0 }}>
    <DataTable fields={fields} rows={rows} label='Dataset preview' contextKey={contextKey} />
  </div>
}

function activeCell(grid: HTMLElement) {
  return document.getElementById(grid.getAttribute('aria-activedescendant') ?? '')
}

afterEach(async () => {
  document.documentElement.classList.remove('dark')
  await page.viewport(1280, 720)
})

describe('shared typed dataset table', () => {
  it('preserves exact numbers, zoned timestamps, empty strings and text escaping', async () => {
    await page.viewport(1920, 720)
    const fields = [field('integer', 'integer'), field('decimal', 'decimal'), field('date', 'timestamp'),
      field('null'), field('empty'), field('html'), field('boolean', 'boolean')]
    const rows: DatasetPreview['rows'] = [[
      { type: 'integer', value: '9223372036854775807' },
      { type: 'decimal', value: '1234567890123456789012345678.1234567890' },
      { type: 'timestamp', value: '2026-11-14T01:02:03.123456+07:00' },
      null, { type: 'string', value: '' },
      { type: 'string', value: '<img src=x onerror="alert(1)">' },
      { type: 'boolean', value: false },
    ]]
    const screen = await render(frame(fields, rows))
    const grid = screen.getByRole('grid').element() as HTMLElement
    grid.focus()
    for (let index = 0; index < fields.length; index++) {
      if (index) await userEvent.keyboard('{ArrowRight}')
      const value = rows[0][index]
      const expected = value === null ? 'NULL' : value.value === '' ? 'Empty string' : String(value.value)
      await expect.poll(() => activeCell(grid)?.textContent).toBe(expected)
    }
    expect(grid.querySelector('img')).toBeNull()
    expect(grid.querySelectorAll('button')).toHaveLength(0)
    await page.screenshot({ path: '__screenshots__/native-table-exact-values.png' })
  })

  it.each(['light', 'dark'])('virtualizes both axes at narrow width in %s theme', async theme => {
    await page.viewport(320, 640)
    document.documentElement.classList.toggle('dark', theme === 'dark')
    const fields = Array.from({ length: 512 }, (_, column) => field(`field_${column}`))
    const rows: DatasetPreview['rows'] = Array.from({ length: 200 }, (_, row) =>
      fields.map((_, column) => ({ type: 'string' as const, value: `row ${row}, column ${column}` })))
    const screen = await render(frame(fields, rows))
    const grid = screen.getByRole('grid').element() as HTMLElement
    await expect.poll(() => grid.querySelectorAll('[role=gridcell]').length).toBeGreaterThan(0)
    expect(grid.querySelectorAll('[role=gridcell]').length).toBeLessThan(150)
    expect(grid.scrollWidth).toBeGreaterThan(grid.clientWidth)
    expect(grid.scrollHeight).toBeGreaterThan(grid.clientHeight)
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320)
    grid.focus()
    await userEvent.keyboard('{Control>}{End}{/Control}')
    await expect.poll(() => activeCell(grid)?.textContent).toBe('row 199, column 511')
    await expect.poll(() => grid.scrollTop).toBeGreaterThan(6000)
    await expect.poll(() => grid.scrollLeft).toBeGreaterThan(100_000)
    expect(grid.querySelectorAll('[role=gridcell]').length).toBeLessThan(150)
    const bounds = grid.getBoundingClientRect()
    const cell = activeCell(grid)!.getBoundingClientRect()
    expect(cell.top).toBeGreaterThanOrEqual(bounds.top + 36)
    expect(cell.right).toBeLessThanOrEqual(bounds.right)
    await page.screenshot({ path: `__screenshots__/native-table-narrow-${theme}.png` })
    await userEvent.keyboard('{Control>}{Home}{/Control}')
    await expect.poll(() => activeCell(grid)?.textContent).toBe('row 0, column 0')
    await userEvent.keyboard('{PageDown}')
    await expect.poll(() => activeCell(grid)?.textContent).toBe('row 7, column 0')
    await userEvent.keyboard('{ArrowRight}{ArrowUp}')
    await expect.poll(() => activeCell(grid)?.textContent).toBe('row 6, column 1')
  })

  it('resets selection and scroll when the caller changes data or security scope', async () => {
    const fields = [field('value')]
    const rows: DatasetPreview['rows'] = Array.from({ length: 200 }, (_, row) =>
      [{ type: 'string', value: `previous ${row}` }])
    const screen = await render(frame(fields, rows))
    const grid = screen.getByRole('grid').element() as HTMLElement
    grid.focus()
    await userEvent.keyboard('{Control>}{End}{/Control}')
    await expect.poll(() => activeCell(grid)?.textContent).toBe('previous 199')
    await screen.rerender(frame(fields, [[{ type: 'string', value: 'current role' }]], 'dataset:1:epoch:2'))
    const next = screen.getByRole('grid').element() as HTMLElement
    await expect.poll(() => activeCell(next)?.textContent).toBe('current role')
    expect(next.scrollTop).toBe(0)
    expect(next.textContent).not.toContain('previous')
  })

  it('reports an empty result without inventing sample rows', async () => {
    const screen = await render(frame([field('value')], []))
    await expect.element(screen.getByRole('status')).toHaveTextContent('No rows returned.')
    expect(document.querySelector('[role=gridcell]')).toBeNull()
  })
})
