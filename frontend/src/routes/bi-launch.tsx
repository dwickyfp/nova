import { z } from 'zod'
import { createFileRoute, redirect } from '@tanstack/react-router'
import { BiLaunch } from '@/features/bi/bi-launch'
import { useAuthStore } from '@/stores/auth-store'

/**
 * Entry point into Superset (served at `/bi/`). Superset sends signed-out
 * browsers here; Nova signs them in, opens a BI session and returns them to
 * the Superset page they asked for.
 */
const searchSchema = z.object({
  next: z.string().max(2048).optional(),
})

export const Route = createFileRoute('/bi-launch')({
  beforeLoad: () => {
    const { accessToken } = useAuthStore.getState().auth
    if (!accessToken && window.self !== window.top) {
      // Superset sent its frame here after the Nova session ended. Sign in
      // at the top level, then come back to the framed BI page.
      window.top?.location.assign('/sign-in?redirect=%2Fanalytics')
      throw redirect({ to: '/sign-in', search: { redirect: '/analytics' } })
    }
    if (!accessToken) {
      throw redirect({
        to: '/sign-in',
        search: { redirect: window.location.pathname + window.location.search },
      })
    }
  },
  validateSearch: searchSchema,
  component: RouteComponent,
})

function RouteComponent() {
  const { next } = Route.useSearch()
  return <BiLaunch next={next} />
}
