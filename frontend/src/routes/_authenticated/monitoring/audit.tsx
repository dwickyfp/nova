import { createFileRoute } from '@tanstack/react-router'
import { MonitoringAuditTrail } from '@/features/monitoring/audit-trail'

export const Route = createFileRoute('/_authenticated/monitoring/audit')({
  component: MonitoringAuditTrail,
})
