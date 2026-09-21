import { createFileRoute, redirect } from '@tanstack/react-router'

/**
 * The in-console Studio entry now lives at the standalone `/studio`. This route
 * stays as a redirect so an existing link or bookmark keeps working.
 */
export const Route = createFileRoute('/_authenticated/agents/studio')({
  beforeLoad: () => {
    throw redirect({ to: '/studio' })
  },
})
