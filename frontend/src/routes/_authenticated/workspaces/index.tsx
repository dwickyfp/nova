import { createFileRoute } from '@tanstack/react-router'
import { WorkspacesPage } from '@/features/workspaces'

export const Route = createFileRoute('/_authenticated/workspaces/')({
  component: WorkspacesPage,
})
