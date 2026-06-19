import { createFileRoute } from '@tanstack/react-router'
import { MonitoringDataLoads } from '@/features/monitoring/data-loads'

export const Route = createFileRoute('/_authenticated/monitoring/loads')({
  component: MonitoringDataLoads,
})
