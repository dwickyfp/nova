import { z } from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { ReportPage } from '@/features/smart-reports/page'

export const Route = createFileRoute('/studio/reports/$reportId')({
  validateSearch: z.object({ handoff: z.string().max(128).optional(), edit: z.boolean().optional().catch(false) }),
  component: ReportRoute,
})

function ReportRoute() {
  const { reportId } = Route.useParams()
  const { handoff, edit } = Route.useSearch()
  return <ReportPage key={reportId} reportId={reportId} handoff={handoff} edit={edit} />
}
