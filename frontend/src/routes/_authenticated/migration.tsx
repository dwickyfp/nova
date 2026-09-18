import { createFileRoute } from '@tanstack/react-router'
import { MigrationPage } from '@/features/migration'

export const Route = createFileRoute('/_authenticated/migration')({
  component: MigrationPage,
})
