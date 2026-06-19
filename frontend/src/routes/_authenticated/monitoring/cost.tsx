import { createFileRoute } from '@tanstack/react-router'
import { MonitoringQueryCost } from '@/features/monitoring/query-cost'

export const Route = createFileRoute('/_authenticated/monitoring/cost')({
  component: MonitoringQueryCost,
})
