import { createFileRoute } from '@tanstack/react-router'
import { AgentsPage } from '@/features/agents'

export const Route = createFileRoute('/_authenticated/agents/')({
  component: AgentsPage,
})
