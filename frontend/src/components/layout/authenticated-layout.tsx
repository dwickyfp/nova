import { useEffect } from 'react'
import { Outlet } from '@tanstack/react-router'
import { getCookie } from '@/lib/cookies'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { LayoutProvider } from '@/context/layout-provider'
import { SearchProvider } from '@/context/search-provider'
import { AssistantProvider } from '@/features/assistant/assistant-provider'
import { AssistantDock } from '@/features/assistant/assistant-dock'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { AppSidebar } from '@/components/layout/app-sidebar'
import { SkipToMain } from '@/components/skip-to-main'

type AuthenticatedLayoutProps = {
  children?: React.ReactNode
}

export function AuthenticatedLayout({ children }: AuthenticatedLayoutProps) {
  const defaultOpen = getCookie('sidebar_state') !== 'false'
  const accessToken = useAuthStore((state) => state.auth.accessToken)
  const user = useAuthStore((state) => state.auth.user)
  const setUser = useAuthStore((state) => state.auth.setUser)

  useEffect(() => {
    if (!accessToken || user) return

    void api
      .get<{
        username: string
        roles: string[]
        active_role?: string | null
        must_change_password?: boolean
      }>('/auth/me')
      .then(({ username, roles, active_role, must_change_password }) => {
        // A required password change gates every authenticated route. A reload
        // must not let the user skip it: drop the session and send them back to
        // sign-in, where the change flow runs.
        if (must_change_password) {
          useAuthStore.getState().auth.reset()
          window.location.href = '/sign-in'
          return
        }
        setUser({ username, roles, activeRole: active_role ?? roles[0] ?? null })
      })
  }, [accessToken, setUser, user])

  return (
    <SearchProvider>
      <LayoutProvider>
        <AssistantProvider>
          <SidebarProvider defaultOpen={defaultOpen}>
            <SkipToMain />
            <AppSidebar />
            <SidebarInset
              className={cn(
                // Set content container, so we can use container queries
                '@container/content',

                // If layout is fixed, set the height
                // to 100svh to prevent overflow
                'has-data-[layout=fixed]:h-svh',

                // If layout is fixed and sidebar is inset,
                // set the height to 100svh - spacing (total margins) to prevent overflow
                'peer-data-[variant=inset]:has-data-[layout=fixed]:h-[calc(100svh-(var(--spacing)*4))]',

                // Fixed pages own their inner scrolling area.
                'has-data-[layout=fixed]:overflow-hidden'
              )}
            >
              {/* The assistant panel is an inline sibling of the page content, so
                  both keep their own scroll area inside this row. */}
              <div className='flex min-h-0 w-full flex-1'>
                <div className='flex min-h-0 min-w-0 flex-1 flex-col'>{children ?? <Outlet />}</div>
                <AssistantDock />
              </div>
            </SidebarInset>
          </SidebarProvider>
        </AssistantProvider>
      </LayoutProvider>
    </SearchProvider>
  )
}
