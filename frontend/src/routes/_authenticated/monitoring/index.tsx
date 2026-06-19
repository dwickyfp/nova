import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/_authenticated/monitoring/')({
  beforeLoad: async () => {
    throw redirect({ to: '/query-history' })
  },
})
