import { type ReactNode, useMemo, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  MoreHorizontal,
  RefreshCw,
  Shield,
} from 'lucide-react'
import { toast } from 'sonner'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { api } from '@/lib/api-client'

type DefaultRoleMode = 'explicit' | 'all' | 'none'

type UserDetailResponse = {
  username: string
  host: string
  identity: string
  is_protected: boolean
  roles: string[]
  default_roles: string[]
  default_role_mode: DefaultRoleMode
  auth_plugin: string | null
  auth_mode: string
  password_enabled: boolean
  last_login: string | null
  properties: Record<string, string>
}

const PAGE_SIZE = 10

function formatLastLogin(value: string | null) {
  if (!value) return 'Never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function DetailField({
  label,
  value,
}: {
  label: string
  value: ReactNode
}) {
  return (
    <div className='space-y-1.5'>
      <p className='text-sm text-muted-foreground'>{label}</p>
      <div className='text-base text-foreground'>{value}</div>
    </div>
  )
}

export function UserDetailPage({ username }: { username: string }) {
  const navigate = useNavigate()
  const [searchQuery, setSearchQuery] = useState('')
  const [page, setPage] = useState(1)

  const detailQuery = useQuery<UserDetailResponse>({
    queryKey: ['user-detail', username],
    queryFn: () => api.get<UserDetailResponse>(`/users/${encodeURIComponent(username)}/detail`),
  })

  const detail = detailQuery.data
  const filteredRoles = useMemo(() => {
    const roles = detail?.roles ?? []
    const query = searchQuery.trim().toLowerCase()
    if (!query) return roles
    return roles.filter((role) => role.toLowerCase().includes(query))
  }, [detail?.roles, searchQuery])

  const total = filteredRoles.length
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const pageStart = total === 0 ? 0 : (currentPage - 1) * PAGE_SIZE + 1
  const pageEnd = Math.min(currentPage * PAGE_SIZE, total)
  const visibleRoles = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return filteredRoles.slice(start, start + PAGE_SIZE)
  }, [currentPage, filteredRoles])

  return (
    <>
      <Header fixed>
        <Search className='me-auto' />
      </Header>

      <Main className='flex flex-1 flex-col gap-6'>
        <div className='flex items-start justify-between gap-3'>
          <div className='flex items-center gap-3'>
            <Button
              variant='outline'
              size='icon'
              className='h-10 w-10'
              onClick={() => navigate({ to: '/users' })}
            >
              <ArrowLeft className='h-4 w-4' />
            </Button>
            <div className='space-y-1'>
              <h1 className='text-3xl font-semibold tracking-tight uppercase'>
                {detail?.username ?? username}
              </h1>
              <p className='text-sm text-muted-foreground'>
                User detail and granted roles overview.
              </p>
            </div>
          </div>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant='outline' size='icon' className='h-10 w-10'>
                <MoreHorizontal className='h-4 w-4' />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align='end' className='w-44'>
              <DropdownMenuItem onSelect={() => detailQuery.refetch()}>
                Refresh
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => navigate({ to: '/users' })}>
                Back to users
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        <section className='rounded-2xl border border-border bg-card/60 p-6'>
          <div className='mb-6 flex items-center gap-2'>
            <Shield className='h-4 w-4 text-muted-foreground' />
            <h2 className='text-xl font-semibold'>About</h2>
          </div>

          {detailQuery.isLoading ? (
            <div className='py-10 text-sm text-muted-foreground'>Loading user detail...</div>
          ) : detailQuery.error ? (
            <div className='space-y-3 py-6'>
              <p className='text-sm text-destructive'>
                {detailQuery.error instanceof Error ? detailQuery.error.message : 'Failed to load user detail'}
              </p>
              <Button
                variant='outline'
                onClick={() => {
                  toast.dismiss()
                  void detailQuery.refetch()
                }}
              >
                <RefreshCw className='h-4 w-4' />
                Retry
              </Button>
            </div>
          ) : detail ? (
            <div className='grid gap-x-8 gap-y-8 md:grid-cols-2 xl:grid-cols-3'>
              <DetailField label='Login name' value={detail.username} />
              <DetailField label='Display name' value={detail.username} />
              <DetailField
                label='Default role'
                value={detail.default_roles[0] ? detail.default_roles[0] : 'None'}
              />
              <DetailField label='Last login' value={formatLastLogin(detail.last_login)} />
              <DetailField label='Status' value='Enabled' />
              <DetailField
                label='Roles'
                value={
                  detail.roles.length ? (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant='link' className='h-auto p-0 text-base font-normal'>
                          Granted roles ({detail.roles.length})
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align='start' className='w-56'>
                        {detail.roles.map((role) => (
                          <DropdownMenuItem key={`${detail.identity}-${role}`} disabled>
                            {role}
                          </DropdownMenuItem>
                        ))}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  ) : (
                    'Granted roles (0)'
                  )
                }
              />
            </div>
          ) : null}
        </section>

        <section className='rounded-2xl border border-border bg-card/60 p-6'>
          <div className='mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4'>
            <div className='space-y-1'>
              <p className='text-sm font-medium text-primary'>Roles</p>
              <h2 className='text-2xl font-semibold'>
                {(detail?.username ?? username).toUpperCase()} has {total} roles
              </h2>
            </div>
            <Input
              value={searchQuery}
              onChange={(event) => {
                setSearchQuery(event.target.value)
                setPage(1)
              }}
              placeholder='Search'
              className='w-full max-w-xs'
            />
          </div>

          <div className='relative rounded-md border border-border'>
            {detailQuery.isFetching && !detailQuery.isLoading ? (
              <div className='pointer-events-none absolute inset-x-4 top-4 z-10 flex justify-center'>
                <div className='w-full max-w-xs overflow-hidden rounded-full border border-border bg-background/95 shadow-lg backdrop-blur-sm'>
                  <div className='h-1.5 w-full overflow-hidden bg-muted'>
                    <div className='h-full w-1/3 animate-pulse rounded-full bg-primary' />
                  </div>
                  <div className='px-3 py-2 text-center text-xs font-medium text-foreground'>
                    Refreshing user detail...
                  </div>
                </div>
              </div>
            ) : null}
            <div className='overflow-x-auto'>
              <table className='w-full'>
                <thead className='border-b border-border bg-muted/50'>
                  <tr>
                    <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                      Role Name
                    </th>
                    <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                      Assignment
                    </th>
                    <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                      Default Role
                    </th>
                    <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {detailQuery.isLoading ? (
                    <tr>
                      <td colSpan={4} className='px-4 py-12 text-center text-sm text-muted-foreground'>
                        Loading roles...
                      </td>
                    </tr>
                  ) : visibleRoles.length === 0 ? (
                    <tr>
                      <td colSpan={4} className='px-4 py-12 text-center text-sm text-muted-foreground'>
                        No roles found
                      </td>
                    </tr>
                  ) : (
                    visibleRoles.map((role) => (
                      <tr
                        key={role}
                        className='border-b border-border transition-colors hover:bg-muted/50'
                      >
                        <td className='px-4 py-3 text-sm font-medium'>{role}</td>
                        <td className='px-4 py-3 text-sm text-muted-foreground'>
                          Role grant
                        </td>
                        <td className='px-4 py-3 text-sm'>
                          {detail?.default_roles.includes(role) ? 'Yes' : 'No'}
                        </td>
                        <td className='px-4 py-3 text-right'>
                          <Badge
                            variant='secondary'
                            className='border-transparent bg-emerald-600 text-xs text-white hover:bg-emerald-600'
                          >
                            Assigned
                          </Badge>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className='mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between'>
            <div className='text-sm text-muted-foreground'>
              Showing {pageStart}-{pageEnd} of {total}
            </div>
            <div className='flex flex-col gap-2 sm:flex-row sm:items-center'>
              <span className='text-sm text-muted-foreground'>
                Page {currentPage} of {totalPages}
              </span>
              <div className='flex items-center gap-1'>
                <Button
                  variant='outline'
                  size='icon'
                  onClick={() => setPage(1)}
                  disabled={currentPage === 1}
                  className='h-8 w-8'
                >
                  <ChevronsLeft className='h-4 w-4' />
                </Button>
                <Button
                  variant='outline'
                  size='icon'
                  onClick={() => setPage((value) => Math.max(value - 1, 1))}
                  disabled={currentPage === 1}
                  className='h-8 w-8'
                >
                  <ChevronLeft className='h-4 w-4' />
                </Button>
                <Button
                  variant='outline'
                  size='icon'
                  onClick={() => setPage((value) => Math.min(value + 1, totalPages))}
                  disabled={currentPage >= totalPages}
                  className='h-8 w-8'
                >
                  <ChevronRight className='h-4 w-4' />
                </Button>
                <Button
                  variant='outline'
                  size='icon'
                  onClick={() => setPage(totalPages)}
                  disabled={currentPage >= totalPages}
                  className='h-8 w-8'
                >
                  <ChevronsRight className='h-4 w-4' />
                </Button>
              </div>
            </div>
          </div>
        </section>
      </Main>
    </>
  )
}
