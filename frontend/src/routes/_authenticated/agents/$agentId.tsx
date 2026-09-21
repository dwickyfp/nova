import { createFileRoute } from '@tanstack/react-router'
import { AgentBuilderPage } from '@/features/agents/agent-builder-page'

export const Route = createFileRoute('/_authenticated/agents/$agentId')({
  component: AgentBuilderPage,
})
