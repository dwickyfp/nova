import { afterEach, expect, it, vi } from 'vitest'
import { page, userEvent } from 'vitest/browser'
import { render } from 'vitest-browser-react'
import { api } from '@/lib/api-client'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { SidebarProvider } from '@/components/ui/sidebar'
import { DecisionTab } from './decision-tab'
import '@/styles/index.css'

function contrast(foreground: string, background: string) {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const context = canvas.getContext('2d')!
  const luminance = (color: string) => {
    context.clearRect(0, 0, 1, 1)
    context.fillStyle = color
    context.fillRect(0, 0, 1, 1)
    const channels = [...context.getImageData(0, 0, 1, 1).data]
      .slice(0, 3)
      .map((value) => {
        const channel = value / 255
        return channel <= 0.04045
          ? channel / 12.92
          : ((channel + 0.055) / 1.055) ** 2.4
      })
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
  }
  const values = [luminance(foreground), luminance(background)].sort(
    (a, b) => b - a,
  )
  return (values[0] + 0.05) / (values[1] + 0.05)
}

it.each([320, 1280])(
  'keeps the header visible and scrolls settings at %ipx',
  async (width) => {
    await page.viewport(width, 500)
    mockRegistry()
    const screen = await render(
      <SidebarProvider>
        <div
          style={{ height: '100dvh' }}
          className="flex min-h-0 w-full min-w-0 flex-col overflow-hidden"
        >
          <Header fixed>
            <span>AI Providers</span>
          </Header>
          <Main scroll>
            <DecisionTab />
          </Main>
        </div>
      </SidebarProvider>,
    )
    await expect
      .element(screen.getByRole('switch', { name: 'Decision mode' }))
      .toBeInTheDocument()
    const main = document.querySelector('main')!
    const header = document.querySelector('header')!
    expect(main.scrollHeight).toBeGreaterThan(main.clientHeight)
    main.scrollTop = main.scrollHeight
    expect(header.getBoundingClientRect().top).toBe(0)
    expect(
      document.documentElement.scrollHeight,
      JSON.stringify(
        Array.from(document.body.children).map((el) => ({
          tag: el.tagName,
          rect: el.getBoundingClientRect().toJSON(),
        })),
      ),
    ).toBeLessThanOrEqual(window.innerHeight)
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
      window.innerWidth,
    )
    await page.screenshot()
  },
)

const defaults = {
  enabled: false,
  decision_model_id: null,
  light_model_id: null,
  heavy_model_id: null,
  min_probability: 0.85,
  min_confidence: 0.6,
  timeout_seconds: 3,
}
const providers = [
  { id: 'd', name: 'Kenari Decision', type: 'decision', is_active: true },
  { id: 'p', name: 'Answers', type: 'openai_compatible', is_active: true },
]
const models = {
  d: [{ id: 'jev', name: 'jev-1-13-free', type: 'decision', is_active: true }],
  p: [
    { id: 'light', name: 'Fast', type: 'llm', is_active: true },
    { id: 'heavy', name: 'Reasoning', type: 'llm', is_active: true },
    { id: 'embed', name: 'Embedding', type: 'embedding', is_active: true },
  ],
}

afterEach(() => {
  vi.restoreAllMocks()
  document.documentElement.classList.remove('dark')
})

function mockRegistry(config: object = defaults, empty = false) {
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/ai/decision-settings') return config
    if (path === '/ai/providers') return { providers: empty ? [] : providers }
    return { models: path.includes('/d/') ? models.d : models.p }
  })
  return vi.spyOn(api, 'put').mockImplementation(async (_path, body) => body)
}

it.each([
  { width: 320, dark: false },
  { width: 1280, dark: true },
])(
  'configures, saves and disables decision mode at $width px',
  async ({ width, dark }) => {
    await page.viewport(width, 850)
    document.documentElement.classList.toggle('dark', dark)
    const put = mockRegistry()
    const screen = await render(<DecisionTab />)
    const toggle = screen.getByRole('switch', { name: 'Decision mode' })
    await expect.element(toggle).not.toBeChecked()
    await toggle.click()
    await expect
      .element(screen.getByRole('button', { name: 'Save settings' }))
      .toBeDisabled()
    for (const [label, choice] of [
      ['Decision model', 'Kenari Decision / jev-1-13-free'],
      ['Light workload model', 'Answers / Fast'],
      ['Heavy workload model', 'Answers / Reasoning'],
    ]) {
      await screen.getByRole('combobox', { name: label }).click()
      await expect
        .element(screen.getByRole('option', { name: /embedding/i }))
        .not.toBeInTheDocument()
      await screen.getByRole('option', { name: choice, exact: true }).click()
    }
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width)
    await expect.element(screen.getByRole('listbox')).not.toBeInTheDocument()
    const help = getComputedStyle(
      document.querySelector('#decision-mode-help')!,
    )
    const body = getComputedStyle(document.body)
    const button = getComputedStyle(
      document.querySelector('button[type="submit"]')!,
    )
    const helpContrast = contrast(help.color, body.backgroundColor)
    const buttonContrast = contrast(button.color, button.backgroundColor)
    expect(helpContrast).toBeGreaterThanOrEqual(4.5)
    expect(buttonContrast).toBeGreaterThanOrEqual(4.5)
    await screen.getByRole('button', { name: 'Save settings' }).hover()
    const hover = getComputedStyle(
      document.querySelector('button[type="submit"]')!,
    )
    expect(contrast(hover.color, hover.backgroundColor)).toBeGreaterThanOrEqual(
      4.5,
    )
    console.info(JSON.stringify({ dark, helpContrast, buttonContrast }))
    await page.screenshot()
    await screen.getByRole('button', { name: 'Save settings' }).click()
    expect(put).toHaveBeenLastCalledWith('/ai/decision-settings', {
      ...defaults,
      enabled: true,
      decision_model_id: 'jev',
      light_model_id: 'light',
      heavy_model_id: 'heavy',
    })
    await expect
      .element(screen.getByText('Unsaved changes'))
      .not.toBeInTheDocument()
    const element = document.querySelector('[role="switch"]') as HTMLElement
    element.focus()
    await userEvent.keyboard(' ')
    await expect.element(toggle).not.toBeChecked()
    await userEvent.keyboard('{Tab}')
    expect(document.activeElement?.id).toBe('decision_model_id')
    await userEvent.keyboard('{Enter}')
    await expect.element(screen.getByRole('listbox')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    await expect.element(screen.getByRole('listbox')).not.toBeInTheDocument()
    await screen.getByRole('button', { name: 'Save settings' }).click()
    expect(put).toHaveBeenLastCalledWith(
      '/ai/decision-settings',
      expect.objectContaining({ enabled: false }),
    )
  },
)

it('can disable after all selected models disappear', async () => {
  const put = mockRegistry(
    {
      ...defaults,
      enabled: true,
      decision_model_id: 'gone',
      light_model_id: 'gone-light',
      heavy_model_id: 'gone-heavy',
    },
    true,
  )
  const screen = await render(<DecisionTab />)
  await screen.getByRole('switch', { name: 'Decision mode' }).click()
  await screen.getByRole('button', { name: 'Save settings' }).click()
  expect(put).toHaveBeenCalledWith(
    '/ai/decision-settings',
    expect.objectContaining({ enabled: false }),
  )
})

it('retries failed loading and retains unsaved changes on save failure', async () => {
  const put = mockRegistry()
  vi.mocked(api.get).mockRejectedValueOnce(new Error('offline'))
  const screen = await render(<DecisionTab />)
  await expect
    .element(screen.getByRole('alert'))
    .toHaveTextContent('Could not load')
  await screen.getByRole('button', { name: 'Try again' }).click()
  await screen.getByRole('combobox', { name: 'Decision model' }).click()
  await screen
    .getByRole('option', { name: 'Kenari Decision / jev-1-13-free' })
    .click()
  put.mockRejectedValueOnce(new Error('forbidden'))
  await screen.getByRole('button', { name: 'Save settings' }).click()
  await expect
    .element(screen.getByRole('alert'))
    .toHaveTextContent('Could not save')
  await expect.element(screen.getByText('Unsaved changes')).toBeInTheDocument()
})
