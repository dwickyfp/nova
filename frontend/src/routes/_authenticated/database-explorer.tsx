import { createFileRoute } from '@tanstack/react-router'
import { DatabaseExplorerPage } from '@/features/database-explorer'

export const Route = createFileRoute('/_authenticated/database-explorer')({
  component: DatabaseExplorerPage,
})
