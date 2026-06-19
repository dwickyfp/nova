import { createFileRoute } from '@tanstack/react-router'
import { MonitoringQueryHistory } from '@/features/monitoring/query-history'

export const Route = createFileRoute('/_authenticated/monitoring/')({
  component: MonitoringQueryHistory,
})
