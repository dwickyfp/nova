import { Fragment } from "react";
import { useLayout } from "@/context/layout-provider";
import { useAuthStore } from "@/stores/auth-store";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import { AppTitle } from "./app-title";
import { sidebarData } from "./data/sidebar-data";
import { NavGroup } from "./nav-group";
import { NavUser } from "./nav-user";

export function AppSidebar() {
  const { collapsible, variant } = useLayout();
  const user = useAuthStore((state) => state.auth.user);

  return (
    <Sidebar collapsible={collapsible} variant={variant}>
      <SidebarHeader>
        <AppTitle />
      </SidebarHeader>
      <SidebarContent>
        {sidebarData.navGroups.map((props, index) => (
          <Fragment key={props.title}>
            {index > 0 && (
              <SidebarSeparator className="hidden w-8 self-center bg-sidebar-border/70 group-data-[collapsible=icon]:block" />
            )}
            <NavGroup {...props} />
          </Fragment>
        ))}
      </SidebarContent>
      <SidebarFooter>
        <NavUser user={user} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
