import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from 'vitest-browser-react'
import { page } from 'vitest/browser'
import { describe, expect, it, vi } from 'vitest'
import '@/styles/index.css'
import { SemanticBuilderPage } from './semantic-builder-page'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
  useNavigate: () => vi.fn(),
}))
vi.mock('@/components/layout/header', () => ({ Header: () => null }))
vi.mock('@/components/layout/main', () => ({
  Main: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('./metadata-api', () => ({
  metadataApi: { listDatabases: vi.fn().mockResolvedValue(['SALES']) },
}))
vi.mock('@/features/intelligence/semantic-views-api', () => ({
  semanticViewsApi: { create: vi.fn() },
}))

describe('Semantic View visual builder', () => {
  it('shows a guided first step and keeps technical output out of the way on mobile', async () => {
    await page.viewport(320, 640)
    try {
      const screen = await render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <SemanticBuilderPage />
        </QueryClientProvider>,
      )
      await expect.element(screen.getByRole('heading', { name: '1. Name this view' })).toBeVisible()
      await expect.element(screen.getByRole('heading', { name: '2. Add a source table' })).toBeVisible()
      await expect.element(screen.getByRole('combobox', { name: 'Source database' })).toBeVisible()
      await expect.element(screen.getByRole('button', { name: 'Create draft' })).toBeDisabled()
      const preview = screen.getByText('Preview generated Ossie YAML (advanced)').element().closest('details')
      expect(preview?.open).toBe(false)
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320)
    } finally {
      await page.viewport(1280, 720)
    }
  })
})
