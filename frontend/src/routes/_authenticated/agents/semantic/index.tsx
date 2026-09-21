import { createFileRoute } from '@tanstack/react-router'
import { SemanticModelsPage } from '@/features/agents/semantic-page'

export const Route = createFileRoute('/_authenticated/agents/semantic/')({  component: SemanticModelsPage,
})
