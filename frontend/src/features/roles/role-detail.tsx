import { type ReactNode, useMemo, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, MoreHorizontal, RefreshCw, Shield } from 'lucide-react'
import { toast } from 'sonner'
import {
  SimpleTablePagination,
  SimpleTableToolbar,
  SimpleTableViewport,
} from '@/components/data-table/simple-table-controls'
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
import { api } from '@/lib/api-client'

// ---------------------------------------------------------------------------
// Types (mirror the API contract from backend/app/modules/users/router.py)
// ---------------------------------------------------------------------------

type RolePrivilege = {
  GRANTEE?: string
  OBJECT_CATALOG?: string | null
  OBJECT_DATABASE?: string | null
  OBJECT_NAME?: string | null
  OBJECT_TYPE?: string | null
  PRIVILEGE_TYPE?: string | null
  IS_GRANTABLE?: string | null
}

type RoleDetail = {
  name: string
  is_builtin: boolean
  is_protected: boolean
  is_mutable: boolean
  privileges: RolePrivilege[]
  grants: string[]
  members: {
    users: Array<{ username: string; host: string; identity: string }>
    nested_roles: string[]
    parent_roles: string[]
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const PAGE_SIZE = 10

function DetailField({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className='space-y-1.5'>
      <p className='text-sm text-muted-foreground'>{label}</p>
      <div className='text-base text-foreground'>{value}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page Component
// ---------------------------------------------------------------------------

export function RoleDetailPage({ name }: { name: string }) {
  const navigate = useNavigate()

  // Privileges table state
  const [privSearch, setPrivSearch] = useState('')
  const [privScopeFilter, setPrivScopeFilter] = useState('')
  const [privGrantableFilter, setPrivGrantableFilter] = useState('')
  const [privPage, setPrivPage] = useState(1)
  const [privPageSize, setPrivPageSize] = useState(PAGE_SIZE)

  // Members table state
  const [memberSearch, setMemberSearch] = useState('')
  const [memberHostFilter, setMemberHostFilter] = useState('')
  const [memberPage, setMemberPage] = useState(1)
  const [memberPageSize, setMemberPageSize] = useState(PAGE_SIZE)

  // ---- Data fetching ----
  const detailQuery = useQuery<RoleDetail>({
    queryKey: ['role-detail', name],
    queryFn: () =>
      api.get<RoleDetail>(`/users/roles/${encodeURIComponent(name)}`),
  })

  const detail = detailQuery.data

  // ---- Privileges filtering + pagination ----
  const filteredPrivileges = useMemo(() => {
    const privileges = detail?.privileges ?? []
    const q = privSearch.trim().toLowerCase()
    return privileges.filter((p) => {
      if (privScopeFilter && p.OBJECT_TYPE !== privScopeFilter) return false
      const isGrantable = p.IS_GRANTABLE?.toUpperCase() === 'YES'
      if (privGrantableFilter === 'Grantable' && !isGrantable) return false
      if (privGrantableFilter === 'Not grantable' && isGrantable) return false
      if (!q) return true
      const haystack = [
        p.PRIVILEGE_TYPE,
        p.OBJECT_TYPE,
        p.OBJECT_CATALOG,
        p.OBJECT_DATABASE,
        p.OBJECT_NAME,
        p.IS_GRANTABLE,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return haystack.includes(q)
    })
  }, [detail?.privileges, privGrantableFilter, privScopeFilter, privSearch])

  const privilegeScopeOptions = useMemo(
    () =>
      Array.from(
        new Set(
          (detail?.privileges ?? [])
            .map((privilege) => privilege.OBJECT_TYPE)
            .filter((value): value is string => Boolean(value))
        )
      ).sort(),
    [detail?.privileges]
  )

  const privTotalPages = Math.max(
    1,
    Math.ceil(filteredPrivileges.length / privPageSize)
  )
  const privCurrentPage = Math.min(privPage, privTotalPages)
  const visiblePrivileges = useMemo(() => {
    const start = (privCurrentPage - 1) * privPageSize
    return filteredPrivileges.slice(start, start + privPageSize)
  }, [privCurrentPage, filteredPrivileges, privPageSize])

  // ---- Members filtering + pagination ----
  const filteredMembers = useMemo(() => {
    const members = detail?.members?.users ?? []
    const q = memberSearch.trim().toLowerCase()
    return members.filter((member) => {
      if (memberHostFilter && member.host !== memberHostFilter) return false
      return (
        !q ||
        member.username.toLowerCase().includes(q) ||
        member.host.toLowerCase().includes(q) ||
        member.identity.toLowerCase().includes(q)
      )
    })
  }, [detail?.members?.users, memberHostFilter, memberSearch])

  const memberHostOptions = useMemo(
    () =>
      Array.from(
        new Set((detail?.members?.users ?? []).map((member) => member.host))
      ).sort(),
    [detail?.members?.users]
  )

  const memberTotalPages = Math.max(
    1,
    Math.ceil(filteredMembers.length / memberPageSize)
  )
  const memberCurrentPage = Math.min(memberPage, memberTotalPages)
  const visibleMembers = useMemo(() => {
    const start = (memberCurrentPage - 1) * memberPageSize
    return filteredMembers.slice(start, start + memberPageSize)
  }, [filteredMembers, memberCurrentPage, memberPageSize])

  // ---- Derived values for the About section ----
  const memberCount = detail?.members?.users?.length ?? 0
  const privilegeCount = detail?.privileges?.length ?? 0

  // =======================================================================
  // Render
  // =======================================================================
  return (
    <>
      <Header fixed>
        <Search className='me-auto' />
      </Header>

      <Main className='flex flex-1 flex-col gap-6'>
        {/* ---------- Top bar ---------- */}
        <div className='flex items-start justify-between gap-3'>
          <div className='flex items-center gap-3'>
            <Button
              variant='outline'
              size='icon'
              className='h-10 w-10'
              onClick={() => navigate({ to: '/roles' })}
            >
              <ArrowLeft className='h-4 w-4' />
            </Button>
            <div className='space-y-1'>
              <h1 className='text-3xl font-semibold tracking-tight uppercase'>
                {detail?.name ?? name}
              </h1>
              <p className='text-sm text-muted-foreground'>
                Role detail, privileges and members overview.
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
              <DropdownMenuItem onSelect={() => navigate({ to: '/roles' })}>
                Back to roles
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        {/* ---------- About section ---------- */}
        <section className='rounded-xl border bg-card p-6'>
          <div className='mb-6 flex items-center gap-2'>
            <Shield className='h-4 w-4 text-muted-foreground' />
            <h2 className='text-xl font-semibold'>About</h2>
          </div>

          {detailQuery.isLoading ? (
            <div className='py-10 text-sm text-muted-foreground'>
              Loading role detail...
            </div>
          ) : detailQuery.error ? (
            <div className='space-y-3 py-6'>
              <p className='text-sm text-destructive'>
                {detailQuery.error instanceof Error
                  ? detailQuery.error.message
                  : 'Failed to load role detail'}
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
              <DetailField label='Role Name' value={detail.name} />
              <DetailField
                label='Type'
                value={detail.is_builtin ? 'Built-in' : 'Custom'}
              />
              <DetailField
                label='Status'
                value={
                  <Badge
                    variant='secondary'
                    className={
                      detail.is_protected
                        ? 'border-transparent bg-amber-600 text-xs text-white hover:bg-amber-600'
                        : 'border-transparent bg-emerald-600 text-xs text-white hover:bg-emerald-600'
                    }
                  >
                    {detail.is_protected ? 'Protected' : 'Not Protected'}
                  </Badge>
                }
              />
              <DetailField
                label='Members'
                value={`${memberCount} user${memberCount === 1 ? '' : 's'}`}
              />
              <DetailField
                label='Privileges'
                value={`${privilegeCount} privilege${privilegeCount === 1 ? '' : 's'}`}
              />
            </div>
          ) : null}
        </section>

        {/* ---------- Privileges table ---------- */}
        <section className='rounded-xl border bg-card p-6'>
          <div className='mb-5 space-y-4 border-b border-border pb-4'>
            <div className='space-y-1'>
              <p className='text-sm font-medium text-primary'>Privileges</p>
              <h2 className='text-2xl font-semibold'>
                {(detail?.name ?? name).toUpperCase()} has{' '}
                {filteredPrivileges.length} privilege
                {filteredPrivileges.length === 1 ? '' : 's'}
              </h2>
            </div>
            <SimpleTableToolbar
              search={privSearch}
              onSearchChange={(value) => {
                setPrivSearch(value)
                setPrivPage(1)
              }}
              searchPlaceholder='Search privileges...'
              resultLabel={`${filteredPrivileges.length} privilege${filteredPrivileges.length !== 1 ? 's' : ''}`}
              filters={[
                {
                  label: 'Scope',
                  value: privScopeFilter,
                  options: privilegeScopeOptions,
                  onChange: (value) => {
                    setPrivScopeFilter(value)
                    setPrivPage(1)
                  },
                  icon: <Shield className='size-3.5' />,
                },
                {
                  label: 'Grantability',
                  value: privGrantableFilter,
                  options: ['Grantable', 'Not grantable'],
                  onChange: (value) => {
                    setPrivGrantableFilter(value)
                    setPrivPage(1)
                  },
                },
              ]}
            />
          </div>

          <SimpleTableViewport className='max-h-[44vh]'>
            {detailQuery.isFetching && !detailQuery.isLoading ? (
              <div className='pointer-events-none absolute inset-x-4 top-4 z-10 flex justify-center'>
                <div className='w-full max-w-xs overflow-hidden rounded-full border border-border bg-background/95 shadow-lg backdrop-blur-sm'>
                  <div className='h-1.5 w-full overflow-hidden bg-muted'>
                    <div className='h-full w-1/3 animate-pulse rounded-full bg-primary' />
                  </div>
                  <div className='px-3 py-2 text-center text-xs font-medium text-foreground'>
                    Refreshing role detail...
                  </div>
                </div>
              </div>
            ) : null}
            <table className='w-full'>
              <thead>
                <tr>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Privilege Type
                  </th>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Scope (Object Type)
                  </th>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Catalog
                  </th>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Database
                  </th>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Object Name
                  </th>
                  <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                    Grantable
                  </th>
                </tr>
              </thead>
              <tbody>
                {detailQuery.isLoading ? (
                  <tr>
                    <td
                      colSpan={6}
                      className='px-4 py-12 text-center text-sm text-muted-foreground'
                    >
                      Loading privileges...
                    </td>
                  </tr>
                ) : visiblePrivileges.length === 0 ? (
                  <tr>
                    <td
                      colSpan={6}
                      className='px-4 py-12 text-center text-sm text-muted-foreground'
                    >
                      No privileges found
                    </td>
                  </tr>
                ) : (
                  visiblePrivileges.map((priv, idx) => (
                    <tr
                      key={`${priv.PRIVILEGE_TYPE}-${priv.OBJECT_TYPE}-${priv.OBJECT_NAME}-${idx}`}
                      className='border-b border-border transition-colors hover:bg-muted/50'
                    >
                      <td className='px-4 py-3 text-sm font-medium'>
                        {priv.PRIVILEGE_TYPE ?? '—'}
                      </td>
                      <td className='px-4 py-3 text-sm text-muted-foreground'>
                        {priv.OBJECT_TYPE ?? '—'}
                      </td>
                      <td className='px-4 py-3 text-sm text-muted-foreground'>
                        {priv.OBJECT_CATALOG ?? '—'}
                      </td>
                      <td className='px-4 py-3 text-sm text-muted-foreground'>
                        {priv.OBJECT_DATABASE ?? '—'}
                      </td>
                      <td className='px-4 py-3 text-sm text-muted-foreground'>
                        {priv.OBJECT_NAME ?? '—'}
                      </td>
                      <td className='px-4 py-3 text-right'>
                        <Badge
                          variant='secondary'
                          className={
                            priv.IS_GRANTABLE?.toUpperCase() === 'YES'
                              ? 'border-transparent bg-emerald-600 text-xs text-white hover:bg-emerald-600'
                              : 'border-transparent bg-muted text-xs text-muted-foreground hover:bg-muted'
                          }
                        >
                          {priv.IS_GRANTABLE?.toUpperCase() === 'YES'
                            ? 'Yes'
                            : 'No'}
                        </Badge>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </SimpleTableViewport>

          <SimpleTablePagination
            total={filteredPrivileges.length}
            page={privCurrentPage}
            pageSize={privPageSize}
            onPageChange={setPrivPage}
            onPageSizeChange={(value) => {
              setPrivPageSize(value)
              setPrivPage(1)
            }}
          />
        </section>

        {/* ---------- Members table ---------- */}
        <section className='rounded-xl border bg-card p-6'>
          <div className='mb-5 space-y-4 border-b border-border pb-4'>
            <div className='space-y-1'>
              <p className='text-sm font-medium text-primary'>Members</p>
              <h2 className='text-2xl font-semibold'>
                {(detail?.name ?? name).toUpperCase()} has{' '}
                {filteredMembers.length} member
                {filteredMembers.length === 1 ? '' : 's'}
              </h2>
            </div>
            <SimpleTableToolbar
              search={memberSearch}
              onSearchChange={(value) => {
                setMemberSearch(value)
                setMemberPage(1)
              }}
              searchPlaceholder='Search members...'
              resultLabel={`${filteredMembers.length} member${filteredMembers.length !== 1 ? 's' : ''}`}
              filters={[
                {
                  label: 'Host',
                  value: memberHostFilter,
                  options: memberHostOptions,
                  onChange: (value) => {
                    setMemberHostFilter(value)
                    setMemberPage(1)
                  },
                },
              ]}
            />
          </div>

          <SimpleTableViewport className='max-h-[44vh]'>
            {detailQuery.isFetching && !detailQuery.isLoading ? (
              <div className='pointer-events-none absolute inset-x-4 top-4 z-10 flex justify-center'>
                <div className='w-full max-w-xs overflow-hidden rounded-full border border-border bg-background/95 shadow-lg backdrop-blur-sm'>
                  <div className='h-1.5 w-full overflow-hidden bg-muted'>
                    <div className='h-full w-1/3 animate-pulse rounded-full bg-primary' />
                  </div>
                  <div className='px-3 py-2 text-center text-xs font-medium text-foreground'>
                    Refreshing role detail...
                  </div>
                </div>
              </div>
            ) : null}
            <table className='w-full'>
              <thead>
                <tr>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Username
                  </th>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Host
                  </th>
                </tr>
              </thead>
              <tbody>
                {detailQuery.isLoading ? (
                  <tr>
                    <td
                      colSpan={2}
                      className='px-4 py-12 text-center text-sm text-muted-foreground'
                    >
                      Loading members...
                    </td>
                  </tr>
                ) : visibleMembers.length === 0 ? (
                  <tr>
                    <td
                      colSpan={2}
                      className='px-4 py-12 text-center text-sm text-muted-foreground'
                    >
                      No members found
                    </td>
                  </tr>
                ) : (
                  visibleMembers.map((member) => (
                    <tr
                      key={member.identity}
                      className='border-b border-border transition-colors hover:bg-muted/50'
                    >
                      <td className='px-4 py-3 text-sm font-medium'>
                        {member.username}
                      </td>
                      <td className='px-4 py-3 text-sm text-muted-foreground'>
                        {member.host}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </SimpleTableViewport>

          <SimpleTablePagination
            total={filteredMembers.length}
            page={memberCurrentPage}
            pageSize={memberPageSize}
            onPageChange={setMemberPage}
            onPageSizeChange={(value) => {
              setMemberPageSize(value)
              setMemberPage(1)
            }}
          />
        </section>
      </Main>
    </>
  )
}
