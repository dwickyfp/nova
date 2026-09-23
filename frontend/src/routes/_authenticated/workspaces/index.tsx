import { z } from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { WorkspacesPage } from '@/features/workspaces'

const searchSchema = z.object({
  file: z.string().optional(),
  q: z.string().optional(),
  template: z.string().optional(),
})

export const Route = createFileRoute('/_authenticated/workspaces/')({
  validateSearch: searchSchema,
  component: WorkspacesPage,
})
