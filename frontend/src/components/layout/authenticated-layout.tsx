import { useEffect } from "react";
import { Outlet } from "@tanstack/react-router";
import { getCookie } from "@/lib/cookies";
import { api } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/auth-store";
import { LayoutProvider } from "@/context/layout-provider";
import { SearchProvider } from "@/context/search-provider";
import {
  AssistantProvider,
  useAssistant,
} from "@/features/assistant/assistant-provider";
import { AssistantDock } from "@/features/assistant/assistant-dock";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { SkipToMain } from "@/components/skip-to-main";

type AuthenticatedLayoutProps = {
  children?: React.ReactNode;
};

/**
 * The row that holds the page content plus the assistant panel.
 *
 * The panel's transcript must scroll inside the panel, never grow the page. For
 * that the row needs a definite height. Two cases bound it:
 *
 * - `layout=fixed` pages already own an inner scroll area, so the row is a
 *   viewport-height box for them regardless of the panel.
 * - `layout=auto` pages rely on document scrolling, which the sticky header
 *   watches, so they are only bounded while the panel is open — the two columns
 *   then share the viewport and the transcript scrolls inside the panel. With
 *   the panel closed the row returns to natural height and the document scrolls
 *   as before.
 */
function AssistantRow({ children }: { children: React.ReactNode }) {
  const { open } = useAssistant();

  // `layout=fixed` pages are recognised with a `has-data` variant rather than
  // reached through `SidebarInset`: the wrappers between the two still allow the
  // descendant selector to match, and doing it here keeps both cases in one
  // place. When bounded, the row must NOT be `flex-1`: in a column flex parent
  // whose height is content-driven (a `layout=auto` inset), `flex-1` lets the
  // item grow past the viewport, which is exactly what stretched the page.
  const fixedClass =
    "has-data-[layout=fixed]:h-full has-data-[layout=fixed]:overflow-hidden";
  // The panel open on a `layout=auto` page also bounds the row, so the two
  // columns share the viewport instead of the transcript stretching the page.
  const bounded = open;
  return (
    <div
      data-assistant-open={open ? "true" : "false"}
      className={cn(
        "flex w-full min-h-0",
        fixedClass,
        bounded
          ? "h-full shrink-0 overflow-hidden"
          : "flex-1 has-data-[layout=fixed]:shrink-0",
      )}
    >
      <div
        className={cn(
          "flex min-h-0 min-w-0 flex-1 flex-col",
          // Bounded means the row clips to the viewport, so the page content has
          // to be the scroller: otherwise a `layout=auto` page taller than the
          // viewport would be cut off with nowhere to scroll. A `layout=fixed`
          // page already owns its inner scroll area and must not get a second
          // one here.
          bounded
            ? "overflow-y-auto has-data-[layout=fixed]:overflow-visible"
            : undefined,
        )}
      >
        {children}
      </div>
      <AssistantDock />
    </div>
  );
}

export function AuthenticatedLayout({ children }: AuthenticatedLayoutProps) {
  const defaultOpen = getCookie("sidebar_state") !== "false";
  const accessToken = useAuthStore((state) => state.auth.accessToken);
  const user = useAuthStore((state) => state.auth.user);
  const setUser = useAuthStore((state) => state.auth.setUser);

  useEffect(() => {
    if (!accessToken || user) return;

    void api
      .get<{
        username: string;
        roles: string[];
        active_role?: string | null;
        must_change_password?: boolean;
      }>("/auth/me")
      .then(({ username, roles, active_role, must_change_password }) => {
        // A required password change gates every authenticated route. A reload
        // must not let the user skip it: drop the session and send them back to
        // sign-in, where the change flow runs.
        if (must_change_password) {
          useAuthStore.getState().auth.reset();
          window.location.href = "/sign-in";
          return;
        }
        setUser({ username, roles, activeRole: active_role ?? null });
      });
  }, [accessToken, setUser, user]);

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
                "@container/content min-w-0",
                "has-data-[assistant-open=true]:h-svh has-data-[assistant-open=true]:overflow-hidden",
                "md:peer-data-[variant=inset]:has-data-[assistant-open=true]:h-[calc(100svh-(var(--spacing)*4))]",

                // If layout is fixed, set the height
                // to 100svh to prevent overflow
                "has-data-[layout=fixed]:h-svh",

                // If layout is fixed and sidebar is inset,
                // set the height to 100svh - spacing (total margins) to prevent overflow
                "peer-data-[variant=inset]:has-data-[layout=fixed]:h-[calc(100svh-(var(--spacing)*4))]",

                // Fixed pages own their inner scrolling area.
                "has-data-[layout=fixed]:overflow-hidden",
              )}
            >
              {/* The assistant panel is an inline sibling of the page content, so
                  both keep their own scroll area inside this row. `AssistantRow`
                  bounds the row to the viewport while the panel is open: the
                  transcript scrolls inside the panel instead of stretching the
                  page, and the content column keeps its own scroll area. */}
              <AssistantRow>{children ?? <Outlet />}</AssistantRow>
            </SidebarInset>
          </SidebarProvider>
        </AssistantProvider>
      </LayoutProvider>
    </SearchProvider>
  );
}
