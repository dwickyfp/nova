import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { Button } from './button'
import { PageHeader } from './page-header'

describe('PageHeader', () => {
  it('renders the title as the page heading', async () => {
    const { getByRole } = await render(<PageHeader title='Query Cost' />)
    await expect
      .element(getByRole('heading', { level: 1, name: 'Query Cost' }))
      .toBeInTheDocument()
  })

  it('renders the description when given', async () => {
    const { getByText } = await render(
      <PageHeader
        title='Query Cost'
        description='Resource consumption and cost breakdown.'
      />
    )
    await expect
      .element(getByText('Resource consumption and cost breakdown.'))
      .toBeInTheDocument()
  })

  it('renders page actions', async () => {
    const { getByRole } = await render(
      <PageHeader title='Users' actions={<Button>Invite user</Button>} />
    )
    await expect
      .element(getByRole('button', { name: 'Invite user' }))
      .toBeInTheDocument()
  })

  it('omits the description and action wrappers when unused', async () => {
    const { container } = await render(<PageHeader title='Roles' />)
    expect(container.querySelectorAll('p')).toHaveLength(0)
    expect(container.querySelectorAll('button')).toHaveLength(0)
  })
})
