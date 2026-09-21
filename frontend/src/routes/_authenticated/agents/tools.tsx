import { createFileRoute } from '@tanstack/react-router'
import { ToolsRegistryPage } from '@/features/agents/tools-registry-page'

export const Route = createFileRoute('/_authenticated/agents/tools')({
  component: ToolsRegistryPage,
})
