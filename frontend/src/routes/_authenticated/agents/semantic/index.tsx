import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/_authenticated/agents/semantic/')({
  beforeLoad: () => {
    throw redirect({ to: '/semantic-views' })
  },
})
