import { createFileRoute } from '@tanstack/react-router'
import { SemanticBuilderPage } from '@/features/agents/semantic-builder-page'

export const Route = createFileRoute('/_authenticated/semantic-views_/builder')({
  component: SemanticBuilderPage,
})
