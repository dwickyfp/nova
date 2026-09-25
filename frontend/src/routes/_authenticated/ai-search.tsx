import { createFileRoute } from '@tanstack/react-router'
import { IntelligencePage } from '@/features/intelligence/intelligence-page'

export const Route = createFileRoute('/_authenticated/ai-search')({
  component: () => <IntelligencePage section="search" />,
})
