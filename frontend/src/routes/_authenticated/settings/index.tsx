import { createFileRoute } from '@tanstack/react-router'
import { AdminSettingsPage } from '@/features/admin-settings'

export const Route = createFileRoute('/_authenticated/settings/')({
  component: AdminSettingsPage,
})
