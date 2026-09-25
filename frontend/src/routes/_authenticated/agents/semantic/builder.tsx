import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/_authenticated/agents/semantic/builder')({
  beforeLoad: () => {
    throw redirect({ to: '/semantic-views/builder' })
  },
})
