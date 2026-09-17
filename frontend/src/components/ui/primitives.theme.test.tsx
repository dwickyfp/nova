import { AlertTriangle, SearchX, Users } from 'lucide-react'
import { afterEach, describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { EmptyState } from './empty-state'
import { LoadingOverlay } from './loading-overlay'
import { MetricCard } from './metric-card'
import { PageHeader } from './page-header'
import { StatusBadge } from './status-badge'

const setTheme = (mode: 'light' | 'dark') => {
  document.documentElement.classList.remove('light', 'dark')
  document.documentElement.classList.add(mode)
}

afterEach(() => {
  document.documentElement.classList.remove('light', 'dark')
})

const tones = ['success', 'warning', 'info', 'danger', 'neutral', 'primary'] as const

describe('Grup 2 primitives across themes', () => {
  for (const mode of ['light', 'dark'] as const) {
    it(`renders every StatusBadge tone in ${mode}`, async () => {
      setTheme(mode)
      const { container } = await render(
        <div>
          {tones.map((tone) => (
            <StatusBadge key={tone} tone={tone} dot>
              {tone}
            </StatusBadge>
          ))}
        </div>
      )
      const badges = container.querySelectorAll('[data-slot="status-badge"]')
      expect(badges).toHaveLength(tones.length)
      for (const badge of badges) {
        expect(badge.className).not.toMatch(
          /(emerald|teal|sky|amber|red|green|blue)-\d{2,3}/
        )
        expect(badge.className.length).toBeGreaterThan(0)
      }
    })

    it(`renders the full primitive set in ${mode}`, async () => {
      setTheme(mode)
      const { container } = await render(
        <div>
          <PageHeader
            title='Query Cost'
            description='Resource consumption breakdown.'
          />
          <MetricCard
            label='Error Count'
            value='7'
            icon={AlertTriangle}
            tone='danger'
            weight='primary'
          />
          <MetricCard label='Active Connections' value='12' icon={Users} />
          <EmptyState
            icon={SearchX}
            title='No queries match this window'
            description='Widen the time range to see more.'
          />
          <EmptyState
            variant='error'
            icon={AlertTriangle}
            title='Could not load metrics'
            description='The API did not respond. Retry the request.'
          />
          <LoadingOverlay label='Loading metrics' />
        </div>
      )

      expect(container.querySelector('[data-slot="page-header"]')).not.toBeNull()
      expect(container.querySelectorAll('[data-slot="metric-card"]')).toHaveLength(
        2
      )
      expect(container.querySelectorAll('[data-slot="empty-state"]')).toHaveLength(
        2
      )
      expect(container.querySelector('[data-slot="loading-overlay"]')).not.toBeNull()
    })
  }
})
