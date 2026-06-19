import { createFileRoute } from '@tanstack/react-router'
import { MonitoringActiveQueries } from '@/features/monitoring/active-queries'

export const Route = createFileRoute('/_authenticated/monitoring/active')({
  component: MonitoringActiveQueries,
})
