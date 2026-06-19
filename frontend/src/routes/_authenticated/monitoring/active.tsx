import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/_authenticated/monitoring/active')({
  beforeLoad: async () => {
    throw redirect({ to: '/active-query' })
  },
})
