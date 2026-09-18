import { createFileRoute } from '@tanstack/react-router'
import { ClusterMonitorPage } from '@/features/cluster'

export const Route = createFileRoute('/_authenticated/monitoring/cluster')({
  component: ClusterMonitorPage,
})
