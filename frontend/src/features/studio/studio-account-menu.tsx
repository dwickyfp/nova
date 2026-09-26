import { useRef, useState } from "react";
import {
  BookOpen,
  Check,
  ChevronsUpDown,
  LogOut,
  Monitor,
  Moon,
  Search,
  SquareUser,
  Sun,
} from "lucide-react";
import { api } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { useTheme } from "@/context/theme-provider";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SignOutDialog } from "@/components/sign-out-dialog";
import type { StudioIdentity } from "@/features/agents/api";
import { AgentMemoryDialog } from "./agent-memory-dialog";

function getInitials(username: string): string {
  const parts = username.split(/[_\s.-]+/).filter(Boolean);
  if (parts.length > 1) {
    return parts
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase();
  }
  return username.slice(0, 2).toUpperCase();
}

/**
 * Studio's account menu, pinned to the bottom of its rail.
 *
 * It mirrors Nova's own account menu so the two surfaces read as one product,
 * but it is fed by Studio's identity endpoint rather than the global store:
 * Studio runs outside the authenticated layout, so the store is not populated
 * on this route. Switching a role is a session operation, exactly as it is in
 * the main console, and the caller reloads Studio's identity afterwards.
 */
export function StudioAccountMenu({
  identity,
  onIdentityChange,
  collapsed = false,
  memoryAgentId,
}: {
  identity: StudioIdentity | undefined;
  onIdentityChange: () => void;
  /** Icon-only mode, for the collapsed rail. */
  collapsed?: boolean;
  memoryAgentId?: string | null;
}) {
  const [roleOpen, setRoleOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [roleSearch, setRoleSearch] = useState("");
  const { theme, setTheme } = useTheme();

  const username = identity?.username ?? "Loading account";
  const activeRole = identity?.active_role ?? "No role";
  const availableRoles = identity?.roles ?? [];
  const initials = getInitials(username);

  const filteredRoles = availableRoles.filter((r) =>
    r.toLowerCase().includes(roleSearch.toLowerCase()),
  );

  async function handleSwitchRole(role: string) {
    if (!identity || role === activeRole) return;
    await api.post("/auth/switch-role", { role });
    onIdentityChange();
    window.location.reload();
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            ref={triggerRef}
            type="button"
            aria-label={collapsed ? username : undefined}
            className={cn(
              "flex items-center gap-2 rounded-md p-1.5 text-start transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none data-[state=open]:bg-accent",
              collapsed ? "w-auto justify-center" : "w-full",
            )}
          >
            <Avatar className="h-8 w-8 rounded-lg">
              <AvatarFallback className="rounded-lg bg-muted font-semibold">
                {initials}
              </AvatarFallback>
            </Avatar>
            {collapsed ? null : (
              <>
                <div className="grid flex-1 text-start text-sm leading-tight">
                  <span className="truncate font-semibold">{username}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {activeRole}
                  </span>
                </div>
                <ChevronsUpDown className="ms-auto size-4 text-muted-foreground" />
              </>
            )}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          className="w-56 rounded-lg"
          side="top"
          align="start"
          sideOffset={4}
          onCloseAutoFocus={(event) => { if (memoryOpen) event.preventDefault(); }}
        >
          <DropdownMenuLabel className="p-0 font-normal">
            <div className="flex items-center gap-2 px-1 py-1.5 text-start text-sm">
              <Avatar className="h-8 w-8 rounded-lg">
                <AvatarFallback className="rounded-lg bg-muted font-semibold">
                  {initials}
                </AvatarFallback>
              </Avatar>
              <div className="grid flex-1 text-start text-sm leading-tight">
                <span className="truncate font-semibold">{username}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {activeRole}
                </span>
              </div>
            </div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />

          <div className="px-2">
            <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Switch Role
            </span>
          </div>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger className="gap-2">
              <SquareUser className="size-4" />
              <span>{activeRole}</span>
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-48">
              <div className="px-2 py-1.5">
                <div className="relative">
                  <Search className="absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    placeholder="Search roles..."
                    value={roleSearch}
                    onChange={(e) => setRoleSearch(e.target.value)}
                    className="h-7 pl-7 text-xs"
                  />
                </div>
              </div>
              <DropdownMenuSeparator />
              {filteredRoles.length === 0 ? (
                <div className="px-2 py-1.5 text-xs text-muted-foreground">
                  No roles found
                </div>
              ) : (
                filteredRoles.map((role) => (
                  <DropdownMenuItem
                    key={role}
                    className="gap-2"
                    onSelect={() => void handleSwitchRole(role)}
                  >
                    <SquareUser className="size-4" />
                    <span className="flex-1">{role}</span>
                    {role === activeRole && (
                      <Check className="size-4 text-sidebar-accent-foreground" />
                    )}
                  </DropdownMenuItem>
                ))
              )}
            </DropdownMenuSubContent>
          </DropdownMenuSub>

          <DropdownMenuSeparator />

          <DropdownMenuSub>
            <DropdownMenuSubTrigger className="gap-2">
              {theme === "dark" ? (
                <Moon className="size-4" />
              ) : theme === "light" ? (
                <Sun className="size-4" />
              ) : (
                <Monitor className="size-4" />
              )}
              <span>Appearance</span>
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-36">
              {(["light", "dark", "system"] as const).map((t) => (
                <DropdownMenuItem
                  key={t}
                  className="gap-2"
                  onSelect={() => setTheme(t)}
                >
                  {t === "light" && <Sun className="size-4" />}
                  {t === "dark" && <Moon className="size-4" />}
                  {t === "system" && <Monitor className="size-4" />}
                  <span className="flex-1 capitalize">{t}</span>
                  {theme === t && <Check className="size-4" />}
                </DropdownMenuItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>

          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={!memoryAgentId}
            title={!memoryAgentId ? "Select a specialist agent to view its memory" : undefined}
            onSelect={() => setMemoryOpen(true)}
          >
            <BookOpen />
            Memory
          </DropdownMenuItem>

          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            onClick={() => setRoleOpen(true)}
          >
            <LogOut />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <SignOutDialog open={roleOpen} onOpenChange={setRoleOpen} />
      {memoryAgentId ? <AgentMemoryDialog
        key={memoryAgentId}
        agentId={memoryAgentId}
        open={memoryOpen}
        onOpenChange={setMemoryOpen}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          triggerRef.current?.focus();
        }}
      /> : null}
    </>
  );
}
