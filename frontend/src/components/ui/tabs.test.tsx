import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { Tabs, TabsList, TabsTrigger } from './tabs'

describe('Tabs', () => {
  it('slides the shared indicator to the active tab', async () => {
    const { container, getByRole } = await render(
      <Tabs defaultValue='information'>
        <TabsList>
          <TabsTrigger value='information'>Information</TabsTrigger>
          <TabsTrigger value='preview'>Preview Data</TabsTrigger>
        </TabsList>
      </Tabs>
    )
    const indicator = container.querySelector<HTMLElement>(
      '[data-slot="tabs-indicator"]'
    )
    const initialTransform = indicator?.style.transform

    await getByRole('tab', { name: 'Preview Data' }).click()

    await expect
      .poll(
        () =>
          container.querySelector<HTMLElement>('[data-slot="tabs-indicator"]')
            ?.style.transform
      )
      .not.toBe(initialTransform)
    await expect.element(getByRole('tab', { name: 'Preview Data' })).toHaveAttribute(
      'data-state',
      'active'
    )
  })

  it('turns off indicator movement when reduced motion is preferred', async () => {
    const { container } = await render(
      <Tabs defaultValue='first'>
        <TabsList>
          <TabsTrigger value='first'>First</TabsTrigger>
          <TabsTrigger value='second'>Second</TabsTrigger>
        </TabsList>
      </Tabs>
    )
    const indicator = container.querySelector('[data-slot="tabs-indicator"]')

    expect(indicator?.className).toContain('motion-reduce:transition-none')
  })
})
