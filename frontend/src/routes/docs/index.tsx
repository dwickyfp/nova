import { createFileRoute } from '@tanstack/react-router'
import { DocsHome } from '@/features/docs'

export const Route = createFileRoute('/docs/')({
  component: DocsHome,
})
