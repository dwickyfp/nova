import { createFileRoute } from '@tanstack/react-router'
import { MonitoringTasks } from '@/features/monitoring/tasks'

export const Route = createFileRoute('/_authenticated/monitoring/tasks')({
  component: MonitoringTasks,
})
