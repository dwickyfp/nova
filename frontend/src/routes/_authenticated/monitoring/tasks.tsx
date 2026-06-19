import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/_authenticated/monitoring/tasks')({
  beforeLoad: async () => {
    throw redirect({ to: '/tasks' })
  },
})
