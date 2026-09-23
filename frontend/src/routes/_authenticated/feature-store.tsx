import { createFileRoute } from '@tanstack/react-router'
import { IntelligencePage } from '@/features/intelligence/intelligence-page'

export const Route = createFileRoute('/_authenticated/feature-store')({
  component: () => <IntelligencePage initialTab="features" />,
})
