import { createFileRoute } from '@tanstack/react-router'
import { ConnectionsPage } from '@/features/connections'

export const Route = createFileRoute('/_authenticated/connections/')({
  component: ConnectionsPage,
})
