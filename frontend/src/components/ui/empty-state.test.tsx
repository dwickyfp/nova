import { AlertCircle, SearchX } from 'lucide-react'
import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { Button } from './button'
import { EmptyState } from './empty-state'

describe('EmptyState', () => {
  it('states the cause and the next step, not just an absence', async () => {
    const { getByText } = await render(
      <EmptyState
        icon={SearchX}
        title='No users match this filter'
        description='Clear the role filter to see every user.'
      />
    )

    await expect
      .element(getByText('No users match this filter'))
      .toBeInTheDocument()
    await expect
      .element(getByText('Clear the role filter to see every user.'))
      .toBeInTheDocument()
  })

  it('renders a caller-supplied action', async () => {
    const { getByRole } = await render(
      <EmptyState
        title='No stages yet'
        action={<Button>Create stage</Button>}
      />
    )
    await expect
      .element(getByRole('button', { name: 'Create stage' }))
      .toBeInTheDocument()
  })

  it('announces an error state to assistive tech', async () => {
    const { container } = await render(
      <EmptyState
        variant='error'
        icon={AlertCircle}
        title='Could not load roles'
        description='The API did not respond. Retry the request.'
      />
    )
    const root = container.querySelector('[data-slot="empty-state"]')
    expect(root?.getAttribute('role')).toBe('alert')
    expect(root?.getAttribute('data-variant')).toBe('error')
  })

  it('does not render a description node when none is given', async () => {
    const { container } = await render(<EmptyState title='Nothing here' />)
    expect(container.querySelectorAll('p')).toHaveLength(1)
  })
})
