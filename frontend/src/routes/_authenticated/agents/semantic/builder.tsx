import { createFileRoute } from '@tanstack/react-router'
import { SemanticBuilderPage } from '@/features/agents/semantic-builder-page'

export const Route = createFileRoute('/_authenticated/agents/semantic/builder')({
  component: SemanticBuilderPage,
})
