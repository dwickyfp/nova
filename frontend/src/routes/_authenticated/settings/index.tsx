import { z } from 'zod'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { AdminSettingsPage } from '@/features/admin-settings'
import { SETTINGS_TABS } from '@/features/admin-settings/api'

const searchSchema = z.object({
  tab: z.enum(SETTINGS_TABS).optional(),
})

export const Route = createFileRoute('/_authenticated/settings/')({
  validateSearch: searchSchema,
  component: RouteComponent,
})

function RouteComponent() {
  const { tab } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  return (
    <AdminSettingsPage
      tab={tab ?? 'session'}
      onTabChange={(next) => void navigate({ search: { tab: next }, replace: true })}
    />
  )
}
