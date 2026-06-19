import { createFileRoute } from '@tanstack/react-router'
import { MonitoringLayout } from '@/features/monitoring'

export const Route = createFileRoute('/_authenticated/monitoring')({
  component: MonitoringLayout,
})
