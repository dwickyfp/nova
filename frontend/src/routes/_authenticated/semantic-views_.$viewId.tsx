import { createFileRoute } from '@tanstack/react-router'
import { SemanticViewRoutePage } from '@/features/intelligence/semantic-view-route-page'

export const Route = createFileRoute('/_authenticated/semantic-views_/$viewId')({
  component: SemanticViewRoutePage,
})
