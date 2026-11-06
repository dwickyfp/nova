import { z } from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { BusinessOntologyPage } from '@/features/ontology'

const searchSchema = z.object({
  tab: z.enum(['concepts', 'proposals', 'releases', 'domains']).optional().catch(undefined),
})

export const Route = createFileRoute('/_authenticated/business-ontology')({
  validateSearch: searchSchema,
  component: BusinessOntologyPage,
})
