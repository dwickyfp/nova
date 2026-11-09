import { z } from 'zod'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { MonitoringQueries, QUERY_TABS } from '@/features/monitoring/queries'

const searchSchema = z.object({
  tab: z.enum(QUERY_TABS).optional(),
})

export const Route = createFileRoute('/_authenticated/monitoring/queries')({
  validateSearch: searchSchema,
  component: RouteComponent,
})

function RouteComponent() {
  const { tab } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  return (
    <MonitoringQueries
      tab={tab ?? 'running'}
      onTabChange={(next) => void navigate({ search: { tab: next }, replace: true })}
    />
  )
}
