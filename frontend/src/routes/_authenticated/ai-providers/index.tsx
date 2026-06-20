import { createFileRoute } from '@tanstack/react-router'
import { AIProviders } from '@/features/ai-providers'

export const Route = createFileRoute('/_authenticated/ai-providers/')({
  component: AIProviders,
})
