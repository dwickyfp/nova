import { createRef, StrictMode } from 'react'
import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import {
  GridStack,
  type GridStackHandle,
  type GridStackOptions,
} from 'gridstack/dist/react'
import 'gridstack/dist/gridstack.css'

const components = {
  Label: ({ label }: Record<string, unknown>) => <span>{String(label)}</span>,
}

function options(label: string): GridStackOptions {
  return {
    column: 12,
    cellHeight: 72,
    margin: 8,
    animate: false,
    children: [{
      id: 'widget-1', x: 0, y: 0, w: 6, h: 4,
      component: 'Label', props: { label },
    }],
  }
}

describe('pinned GridStack React compatibility', () => {
  it('mounts in StrictMode, restores geometry, and releases its instance on unmount', async () => {
    const reference = createRef<GridStackHandle>()
    const screen = await render(
      <StrictMode>
        <GridStack ref={reference} options={options('Revenue')} components={components} />
      </StrictMode>,
    )
    await expect.element(screen.getByText('Revenue')).toBeVisible()
    const grid = reference.current?.getGrid()
    if (!grid) throw new Error('GridStack did not initialize')
    expect(grid.getColumn()).toBe(12)
    expect(grid.engine.nodes).toHaveLength(1)
    const widget = grid.engine.nodes[0].el
    if (!widget) throw new Error('Widget element was not created')
    grid.update(widget, { x: 6, w: 4, h: 3 })
    const saved = grid.save(false)
    if (!Array.isArray(saved)) throw new Error('Expected widget layout')
    expect(saved[0]).toMatchObject({ id: 'widget-1', x: 6, w: 4, h: 3 })
    await screen.unmount()
    expect(reference.current).toBeNull()
    const restored = await render(
      <GridStack ref={reference} options={{ ...options('Revenue'), children: saved }}
        components={components} />,
    )
    await expect.element(restored.getByText('Revenue')).toBeVisible()
    expect(reference.current?.getGrid()?.engine.nodes[0]).toMatchObject({ x: 6, w: 4, h: 3 })
  })

  it('keeps two editor hosts independent even when widget IDs are equal', async () => {
    const left = createRef<GridStackHandle>()
    const right = createRef<GridStackHandle>()
    const screen = await render(<>
      <GridStack ref={left} options={options('Dataviz')} components={components} />
      <GridStack ref={right} options={options('Studio')} components={components} />
    </>)
    await expect.element(screen.getByText('Dataviz')).toBeVisible()
    await expect.element(screen.getByText('Studio')).toBeVisible()
    left.current?.getGrid()?.removeAll()
    await expect.element(screen.getByText('Dataviz')).not.toBeInTheDocument()
    await expect.element(screen.getByText('Studio')).toBeVisible()
    expect(right.current?.getGrid()?.engine.nodes).toHaveLength(1)
  })

  it('renders registered React content as text without interpreting widget labels as HTML', async () => {
    const label = '<img src=x onerror="window.injected=true">'
    const screen = await render(
      <GridStack options={options(label)} components={components} />,
    )
    await expect.element(screen.getByText(label)).toBeVisible()
    expect(document.querySelector('.grid-stack img')).toBeNull()
  })
})
