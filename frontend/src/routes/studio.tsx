import { z } from 'zod'
import { createFileRoute, redirect } from '@tanstack/react-router'
import { StudioApp } from '@/features/studio'
import { useAuthStore } from '@/stores/auth-store'

/**
 * Nova Studio — a **standalone** surface.
 *
 * This route lives outside `/_authenticated` on purpose: Studio does not inherit
 * Nova's sidebar, header, or content frame, so it renders its own full-page
 * layout like Snowflake CoWork. It still requires a session, checked here with
 * the same store the rest of the app uses; an anonymous visitor is redirected to
 * sign-in and returned to the same Studio URL afterwards.
 */
const searchSchema = z.object({
  agent: z.string().optional(),
  thread: z.string().optional(),
  view: z.enum(['chat', 'artifacts', 'capabilities']).optional(),
})

export const Route = createFileRoute('/studio')({
  beforeLoad: () => {
    const { accessToken } = useAuthStore.getState().auth
    if (!accessToken) {
      throw redirect({
        to: '/sign-in',
        search: { redirect: window.location.pathname + window.location.search },
      })
    }
  },
  validateSearch: searchSchema,
  component: StudioApp,
})
