import { createFileRoute, redirect } from '@tanstack/react-router'

// Merged into Monitoring > Queries; old links land on the matching tab.
export const Route = createFileRoute('/_authenticated/query-cost')({
  beforeLoad: () => {
    throw redirect({ to: '/monitoring/queries', search: { tab: 'cost' } })
  },
})
