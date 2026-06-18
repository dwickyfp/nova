import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import {
  BadgeCheck,
  Bell,
  ChevronsUpDown,
  LogOut,
  Moon,
  Monitor,
  Sun,
  Check,
  SquareUser,
  Search,
} from 'lucide-react'
import useDialogState from '@/hooks/use-dialog-state'
import { type AuthUser } from '@/stores/auth-store'
import { useTheme } from '@/context/theme-provider'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
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
} from '@/components/ui/dropdown-menu'
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar'
import { SignOutDialog } from '@/components/sign-out-dialog'
import { Input } from '@/components/ui/input'

type NavUserProps = {
  user: AuthUser | null
}

export function NavUser({ user }: NavUserProps) {
  const { isMobile } = useSidebar()
  const [open, setOpen] = useDialogState()
  const { theme, setTheme } = useTheme()
  const [roleSearch, setRoleSearch] = useState('')
  const username = user?.username ?? 'Loading account'
  const activeRole = user?.roles.length ? user.roles[0] : 'No role'
  const initials = getInitials(username)

  // For now, roles come from user.roles (single role from StarRocks)
  // In the future, this will be a list of switchable roles
  const availableRoles = user?.roles ?? []

  const filteredRoles = availableRoles.filter((r) =>
    r.toLowerCase().includes(roleSearch.toLowerCase())
  )

  return (
    <>
      <SidebarMenu>
        <SidebarMenuItem>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuButton
                size='lg'
                className='data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground'
              >
                <Avatar className='h-8 w-8 rounded-lg'>
                  <AvatarFallback className='rounded-lg bg-sidebar-accent font-semibold text-sidebar-accent-foreground'>
                    {initials}
                  </AvatarFallback>
                </Avatar>
                <div className='grid flex-1 text-start text-sm leading-tight'>
                  <span className='truncate font-semibold'>{username}</span>
                  <span className='truncate text-xs text-muted-foreground'>
                    {activeRole}
                  </span>
                </div>
                <ChevronsUpDown className='ms-auto size-4' />
              </SidebarMenuButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              className='w-(--radix-dropdown-menu-trigger-width) min-w-56 rounded-lg'
              side={isMobile ? 'bottom' : 'right'}
              align='end'
              sideOffset={4}
            >
              {/* ── User header ── */}
              <DropdownMenuLabel className='p-0 font-normal'>
                <div className='flex items-center gap-2 px-1 py-1.5 text-start text-sm'>
                  <Avatar className='h-8 w-8 rounded-lg'>
                    <AvatarFallback className='rounded-lg bg-sidebar-accent font-semibold text-sidebar-accent-foreground'>
                      {initials}
                    </AvatarFallback>
                  </Avatar>
                  <div className='grid flex-1 text-start text-sm leading-tight'>
                    <span className='truncate font-semibold'>{username}</span>
                    <span className='truncate text-xs text-muted-foreground'>
                      {activeRole}
                    </span>
                  </div>
                </div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />

              {/* ── Role switcher (submenu) ── */}
              <div className='px-2 '>
                <span className='text-[10px] font-medium uppercase tracking-wider text-muted-foreground'>
                  Switch Role
                </span>
              </div>
              <DropdownMenuSub>
                <DropdownMenuSubTrigger className='gap-2'>
                  <SquareUser className='size-4' />
                  <span>{activeRole}</span>
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent className='w-48' side='right' align='start' sideOffset={8}>
                  <div className='px-2 py-1.5'>
                    <div className='relative'>
                      <Search className='absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground' />
                      <Input
                        placeholder='Search roles...'
                        value={roleSearch}
                        onChange={(e) => setRoleSearch(e.target.value)}
                        className='h-7 pl-7 text-xs'
                      />
                    </div>
                  </div>
                  <DropdownMenuSeparator />
                  {filteredRoles.length === 0 ? (
                    <div className='px-2 py-1.5 text-xs text-muted-foreground'>
                      No roles found
                    </div>
                  ) : (
                    filteredRoles.map((role) => (
                      <DropdownMenuItem
                        key={role}
                        className='gap-2'
                        onSelect={() => {
                          // TODO: switch role via API
                          // For now, just show as selected
                        }}
                      >
                        <SquareUser className='size-4' />
                        <span className='flex-1'>{role}</span>
                        {role === activeRole && <Check className='size-4' />}
                      </DropdownMenuItem>
                    ))
                  )}
                </DropdownMenuSubContent>
              </DropdownMenuSub>

              <DropdownMenuSeparator />

              {/* ── Appearance (submenu) ── */}
              <DropdownMenuSub>
                <DropdownMenuSubTrigger className='gap-2'>
                  {theme === 'dark' ? (
                    <Moon className='size-4' />
                  ) : theme === 'light' ? (
                    <Sun className='size-4' />
                  ) : (
                    <Monitor className='size-4' />
                  )}
                  <span>Appearance</span>
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent className='w-36' side='right' align='start' sideOffset={8}>
                  {(['light', 'dark', 'system'] as const).map((t) => (
                    <DropdownMenuItem
                      key={t}
                      className='gap-2'
                      onSelect={() => setTheme(t)}
                    >
                      {t === 'light' && <Sun className='size-4' />}
                      {t === 'dark' && <Moon className='size-4' />}
                      {t === 'system' && <Monitor className='size-4' />}
                      <span className='flex-1 capitalize'>{t}</span>
                      {theme === t && <Check className='size-4' />}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuSubContent>
              </DropdownMenuSub>

              <DropdownMenuSeparator />

              {/* ── Account & Notifications ── */}
              <DropdownMenuItem asChild>
                <Link to='/settings/account'>
                  <BadgeCheck />
                  Account
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link to='/settings/notifications'>
                  <Bell />
                  Notifications
                </Link>
              </DropdownMenuItem>

              <DropdownMenuSeparator />

              {/* ── Sign out ── */}
              <DropdownMenuItem
                variant='destructive'
                onClick={() => setOpen(true)}
              >
                <LogOut />
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarMenuItem>
      </SidebarMenu>

      <SignOutDialog open={!!open} onOpenChange={setOpen} />
    </>
  )
}

function getInitials(username: string): string {
  const parts = username.split(/[_\s.-]+/).filter(Boolean)
  if (parts.length > 1) {
    return parts
      .slice(0, 2)
      .map((part) => part[0])
      .join('')
      .toUpperCase()
  }
  return username.slice(0, 2).toUpperCase()
}
