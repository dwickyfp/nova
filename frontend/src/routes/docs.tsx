import { createFileRoute, Outlet, redirect } from '@tanstack/react-router'
import { DocsLayout } from '@/features/docs'
import { useAuthStore } from '@/stores/auth-store'

/**
 * User documentation. A standalone surface like Studio: it renders its own
 * layout instead of the console's sidebar, and still requires a session.
 */
export const Route = createFileRoute('/docs')({
  beforeLoad: () => {
    const { accessToken } = useAuthStore.getState().auth
    if (!accessToken) {
      throw redirect({
        to: '/sign-in',
        search: { redirect: window.location.pathname + window.location.search },
      })
    }
  },
  component: () => (
    <DocsLayout>
      <Outlet />
    </DocsLayout>
  ),
})
