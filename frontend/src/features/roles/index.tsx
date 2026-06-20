import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Eye,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  Users as UsersIcon,
} from 'lucide-react'
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type RoleSummary = {
  name: string
  is_builtin: boolean
  is_protected: boolean
  is_mutable: boolean
}

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
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 10

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export function RolesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // UI state
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)
  const [createOpen, setCreateOpen] = useState(false)
  const [roleFormName, setRoleFormName] = useState('')
  const [roleSubmitting, setRoleSubmitting] = useState(false)
  const [deletingRole, setDeletingRole] = useState<RoleSummary | null>(null)
  const [deleteSubmitting, setDeleteSubmitting] = useState(false)

  // -------------------------------------------------------------------------
  // Fetch roles list
  // -------------------------------------------------------------------------
  const {
    data: rolesData,
    isLoading,
    isFetching,
    refetch,
  } = useQuery<RoleSummary[]>({
    queryKey: ['roles'],
    queryFn: async () => {
      const response = await api.get<{ roles: RoleSummary[] }>('/users/roles')
      return response.roles ?? []
    },
  })

  const roles = rolesData ?? []

  // -------------------------------------------------------------------------
  // Batch-fetch detail for every role (members count + privileges count)
  // -------------------------------------------------------------------------
  const detailQueries = useQueries({
    queries: roles.map((role) => ({
      queryKey: ['roles', role.name, 'detail'],
      queryFn: async (): Promise<RoleDetail> => {
        return api.get<RoleDetail>(
          `/users/roles/${encodeURIComponent(role.name)}`
        )
      },
      staleTime: 60_000,
    })),
  })

  /** Map from role name → RoleDetail (only populated entries) */
  const detailsMap = useMemo(() => {
    const map = new Map<string, RoleDetail>()
    for (const q of detailQueries) {
      if (q.data) {
        map.set(q.data.name, q.data)
      }
    }
    return map
  }, [detailQueries])

  // -------------------------------------------------------------------------
  // Filtered + paginated list
  // -------------------------------------------------------------------------
  const filteredRoles = useMemo(() => {
    const query = search.trim().toLowerCase()
    return roles.filter((role) => {
      if (typeFilter === 'Built-in' && !role.is_builtin) return false
      if (typeFilter === 'Custom' && role.is_builtin) return false
      if (!query) return true
      return [
        role.name,
        role.is_builtin ? 'built-in' : 'custom',
        role.is_protected ? 'protected' : '',
      ]
        .join(' ')
        .toLowerCase()
        .includes(query)
    })
  }, [search, roles, typeFilter])

  const pageCount = Math.ceil(filteredRoles.length / pageSize)

  // Keep page in range when filters change
  useEffect(() => {
    setPage((current) => Math.min(Math.max(current, 1), Math.max(pageCount, 1)))
  }, [pageCount])

  const visibleRoles = useMemo(() => {
    const start = (page - 1) * pageSize
    return filteredRoles.slice(start, start + pageSize)
  }, [filteredRoles, page, pageSize])

  // -------------------------------------------------------------------------
  // Create role
  // -------------------------------------------------------------------------
  async function handleCreateRole() {
    if (!roleFormName.trim()) {
      toast.error('Role name is required')
      return
    }

    setRoleSubmitting(true)
    try {
      await api.post('/users/roles', { role_name: roleFormName.trim() })
      toast.success('Role created successfully')
      setRoleFormName('')
      setCreateOpen(false)
      await queryClient.invalidateQueries({ queryKey: ['roles'] })
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : 'Failed to create role'
      )
    } finally {
      setRoleSubmitting(false)
    }
  }

  // -------------------------------------------------------------------------
  // Delete role
  // -------------------------------------------------------------------------
  async function handleDeleteRole(role: RoleSummary) {
    setDeleteSubmitting(true)
    try {
      await api.delete(`/users/roles/${encodeURIComponent(role.name)}`)
      toast.success(`Role "${role.name}" deleted`)
      setDeletingRole(null)
      await queryClient.invalidateQueries({ queryKey: ['roles'] })
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : 'Failed to delete role'
      )
    } finally {
      setDeleteSubmitting(false)
    }
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  return (
    <>
      <Header fixed>
        <Search className='me-auto' />
      </Header>

      <Main className='flex flex-1 flex-col gap-6'>
        {/* Page header */}
        <div className='flex flex-wrap items-end justify-between gap-3'>
          <div className='space-y-1'>
            <div className='flex items-center gap-2 text-sm text-muted-foreground'>
              <Shield className='size-4' />
              Administrator
            </div>
            <h1 className='text-2xl font-bold tracking-tight'>Roles</h1>
            <p className='max-w-3xl text-sm text-muted-foreground'>
              Manage StarRocks roles, memberships, and scoped privileges.
            </p>
          </div>
          <div className='flex items-center gap-2'>
            <Button
              variant='outline'
              onClick={() => void refetch()}
              disabled={isFetching}
            >
              <RefreshCw
                className={cn('size-4', isFetching && 'animate-spin')}
              />
              Refresh
            </Button>
            <Button
              onClick={() => {
                setRoleFormName('')
                setCreateOpen(true)
              }}
            >
              <Plus className='size-4' />
              Create Role
            </Button>
          </div>
        </div>

        {/* Search */}
        <SimpleTableToolbar
          search={search}
          onSearchChange={(value) => {
            setSearch(value)
            setPage(1)
          }}
          searchPlaceholder='Search roles...'
          resultLabel={`${filteredRoles.length} role${filteredRoles.length !== 1 ? 's' : ''}`}
          filters={[
            {
              label: 'Type',
              value: typeFilter,
              options: ['Built-in', 'Custom'],
              onChange: (value) => {
                setTypeFilter(value)
                setPage(1)
              },
              icon: <Shield className='size-3.5' />,
            },
          ]}
        />

        {/* Table */}
        <section>
          <SimpleTableViewport>
            {isFetching && !isLoading ? (
              <div className='pointer-events-none absolute inset-x-4 top-4 z-10 flex justify-center'>
                <div className='w-full max-w-xs overflow-hidden rounded-full border border-border bg-background/95 shadow-lg backdrop-blur-sm'>
                  <div className='h-1.5 w-full overflow-hidden bg-muted'>
                    <div className='h-full w-1/3 animate-pulse rounded-full bg-primary' />
                  </div>
                  <div className='px-3 py-2 text-center text-xs font-medium text-foreground'>
                    Refreshing roles...
                  </div>
                </div>
              </div>
            ) : null}

            <table className='w-full'>
              <thead>
                <tr>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Role
                  </th>
                  <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                    Type
                  </th>
                  <th className='px-4 py-3 text-center text-xs font-medium text-muted-foreground'>
                    Members
                  </th>
                  <th className='px-4 py-3 text-center text-xs font-medium text-muted-foreground'>
                    Privileges
                  </th>
                  <th className='w-[100px] px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <tr>
                    <td
                      colSpan={5}
                      className='px-4 py-12 text-center text-sm text-muted-foreground'
                    >
                      Loading roles...
                    </td>
                  </tr>
                ) : visibleRoles.length === 0 ? (
                  <tr>
                    <td
                      colSpan={5}
                      className='px-4 py-12 text-center text-sm text-muted-foreground'
                    >
                      No roles found.
                    </td>
                  </tr>
                ) : (
                  visibleRoles.map((role) => {
                    const detail = detailsMap.get(role.name)
                    const membersCount = detail
                      ? detail.members.users.length +
                        detail.members.nested_roles.length
                      : null
                    const privilegesCount = detail
                      ? detail.privileges.length
                      : null

                    return (
                      <tr
                        key={role.name}
                        className='cursor-pointer border-b border-border transition-colors hover:bg-muted/50'
                        onClick={() =>
                          navigate({
                            to: '/roles/$name',
                            params: { name: role.name },
                          })
                        }
                      >
                        {/* Role name */}
                        <td className='px-4 py-3 text-sm'>
                          <div className='flex flex-wrap items-center gap-2'>
                            <span className='font-medium'>{role.name}</span>
                            {role.is_protected ? (
                              <Badge className='border-red-500/20 bg-red-500/10 text-red-600 dark:text-red-300'>
                                Protected
                              </Badge>
                            ) : null}
                          </div>
                        </td>

                        {/* Type */}
                        <td className='px-4 py-3 text-sm'>
                          {role.is_builtin ? (
                            <Badge
                              variant='outline'
                              className='border-amber-500/20 bg-amber-500/10 text-amber-600 dark:text-amber-300'
                            >
                              Built-in
                            </Badge>
                          ) : (
                            <Badge
                              variant='outline'
                              className='border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300'
                            >
                              Custom
                            </Badge>
                          )}
                        </td>

                        {/* Members */}
                        <td className='px-4 py-3 text-sm text-center'>
                          {membersCount !== null ? (
                            <div className='flex items-center justify-center gap-1.5'>
                              <UsersIcon className='size-3.5 text-muted-foreground' />
                              <span className='tabular-nums'>
                                {membersCount}
                              </span>
                            </div>
                          ) : (
                            <span className='text-xs text-muted-foreground'>
                              —
                            </span>
                          )}
                        </td>

                        {/* Privileges */}
                        <td className='px-4 py-3 text-sm text-center'>
                          {privilegesCount !== null ? (
                            <div className='flex items-center justify-center gap-1.5'>
                              <Shield className='size-3.5 text-muted-foreground' />
                              <span className='tabular-nums'>
                                {privilegesCount}
                              </span>
                            </div>
                          ) : (
                            <span className='text-xs text-muted-foreground'>
                              —
                            </span>
                          )}
                        </td>

                        {/* Actions */}
                        <td className='px-4 py-3 text-sm text-right'>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button
                                variant='ghost'
                                size='icon'
                                className='size-8'
                                onClick={(e) => e.stopPropagation()}
                              >
                                <MoreHorizontal className='size-4' />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align='end'>
                              <DropdownMenuItem
                                onClick={(e) => {
                                  e.stopPropagation()
                                  navigate({
                                    to: '/roles/$name',
                                    params: { name: role.name },
                                  })
                                }}
                              >
                                <Eye className='mr-2 size-4' />
                                View Detail
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                className='text-destructive focus:text-destructive'
                                disabled={!role.is_mutable}
                                onClick={(e) => {
                                  e.stopPropagation()
                                  setDeletingRole(role)
                                }}
                              >
                                <Trash2 className='mr-2 size-4' />
                                Delete
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </SimpleTableViewport>

          <div className='px-5 pb-4'>
            <SimpleTablePagination
              page={page}
              pageSize={pageSize}
              total={filteredRoles.length}
              onPageChange={setPage}
              onPageSizeChange={(value) => {
                setPageSize(value)
                setPage(1)
              }}
            />
          </div>
        </section>
      </Main>

      {/* ----------------------------------------------------------------- */}
      {/* Create Role Dialog                                                 */}
      {/* ----------------------------------------------------------------- */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className='sm:max-w-md'>
          <DialogHeader>
            <DialogTitle>Create Role</DialogTitle>
            <DialogDescription>
              Create a new custom role in StarRocks. You can assign privileges
              and members after creation.
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-4 py-2'>
            <div className='space-y-2'>
              <Label htmlFor='role-name'>Role name</Label>
              <Input
                id='role-name'
                value={roleFormName}
                onChange={(event) => setRoleFormName(event.target.value)}
                placeholder='e.g. analyst_readonly'
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !roleSubmitting) {
                    void handleCreateRole()
                  }
                }}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant='outline'
              onClick={() => setCreateOpen(false)}
              disabled={roleSubmitting}
            >
              Cancel
            </Button>
            <Button
              onClick={() => void handleCreateRole()}
              disabled={roleSubmitting}
            >
              {roleSubmitting ? 'Creating...' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ----------------------------------------------------------------- */}
      {/* Delete Role Confirmation Dialog                                    */}
      {/* ----------------------------------------------------------------- */}
      <Dialog
        open={deletingRole !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingRole(null)
        }}
      >
        <DialogContent className='sm:max-w-md'>
          <DialogHeader>
            <DialogTitle>Delete Role</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete the role{' '}
              <strong>{deletingRole?.name}</strong>? This action cannot be
              undone. All privileges and memberships associated with this role
              will be removed.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant='outline'
              onClick={() => setDeletingRole(null)}
              disabled={deleteSubmitting}
            >
              Cancel
            </Button>
            <Button
              variant='destructive'
              onClick={() => {
                if (deletingRole) void handleDeleteRole(deletingRole)
              }}
              disabled={deleteSubmitting}
            >
              {deleteSubmitting ? 'Deleting...' : 'Delete Role'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
