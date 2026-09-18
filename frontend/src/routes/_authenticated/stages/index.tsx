import { createFileRoute } from '@tanstack/react-router'
import { StagesPage } from '@/features/stages'

export const Route = createFileRoute('/_authenticated/stages/')({
  component: StagesPage,
})
