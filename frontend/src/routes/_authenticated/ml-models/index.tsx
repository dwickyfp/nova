import { createFileRoute } from '@tanstack/react-router'
import { MLModels } from '@/features/ml-models'

export const Route = createFileRoute('/_authenticated/ml-models/')({
  component: MLModels,
})
