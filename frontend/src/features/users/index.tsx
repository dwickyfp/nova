import { useEffect, useMemo, useState } from 'react'
import {
  ChevronsLeft,
  ChevronsRight,
  ChevronLeft,
  ChevronRight,
  Database,
  KeyRound,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  Users as UsersIcon,
} from 'lucide-react'
import { toast } from 'sonner'
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
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { Checkbox } from '@/components/ui/checkbox'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'

type DefaultRoleMode = 'explicit' | 'all' | 'none'
type PrivilegeScope =
  | 'SYSTEM'
  | 'CATALOG'
  | 'DATABASE'
  | 'TABLE'
  | 'VIEW'
  | 'MATERIALIZED VIEW'
type SelectorMode = 'specific' | 'all_databases' | 'all_in_database'

type AdminUser = {
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

type UserAuthDetail = {
  username: string
  host: string
  identity: string
  password_enabled: boolean
  auth_plugin: string | null
  auth_mode: string
  plugin_user: string | null
}

type UserDefaultRolesDetail = {
  username: string
  host: string
  identity: string
  mode: DefaultRoleMode
  roles: string[]
}

type UserDetail = {
  properties: Record<string, string>
  grants: string[]
  authentication: UserAuthDetail
  defaultRoles: UserDefaultRolesDetail
}

type UserFormState = {
  username: string
  password: string
  confirmPassword: string
  host: string
  grantedRoles: string[]
  defaultRole: string
  defaultRoleMode: DefaultRoleMode
  defaultRoles: string[]
  maxUserConnections: string
  catalog: string
  database: string
  sessionProperties: string
}

type RoleActionState = {
  mode: 'grant' | 'revoke'
  user: AdminUser
  role: string
}

type RolePrivilegeFormState = {
  scope: PrivilegeScope
  privilege: string
  selectorMode: SelectorMode
  catalog: string
  database: string
  objectName: string
  withGrantOption: boolean
}

const PAGE_SIZE = 10
const DEFAULT_USER_FORM: UserFormState = {
  username: '',
  password: '',
  confirmPassword: '',
  host: '%',
  grantedRoles: [],
  defaultRole: '',
  defaultRoleMode: 'none',
  defaultRoles: [],
  maxUserConnections: '',
  catalog: '',
  database: '',
  sessionProperties: '',
}
const PRIVILEGE_OPTIONS: Record<PrivilegeScope, string[]> = {
  SYSTEM: [
    'GRANT',
    'NODE',
    'CREATE RESOURCE',
    'PLUGIN',
    'FILE',
    'BLACKLIST',
    'OPERATE',
    'CREATE EXTERNAL CATALOG',
    'REPOSITORY',
    'CREATE RESOURCE GROUP',
    'CREATE GLOBAL FUNCTION',
    'CREATE STORAGE VOLUME',
    'SECURITY',
    'ALL',
  ],
  CATALOG: ['USAGE', 'CREATE DATABASE', 'DROP', 'ALTER', 'ALL'],
  DATABASE: [
    'ALTER',
    'DROP',
    'CREATE TABLE',
    'CREATE VIEW',
    'CREATE FUNCTION',
    'CREATE MATERIALIZED VIEW',
    'ALL',
  ],
  TABLE: ['ALTER', 'DROP', 'SELECT', 'INSERT', 'UPDATE', 'EXPORT', 'DELETE', 'ALL'],
  VIEW: ['SELECT', 'ALTER', 'DROP', 'ALL'],
  'MATERIALIZED VIEW': ['SELECT', 'ALTER', 'REFRESH', 'DROP', 'ALL'],
}
const DEFAULT_PRIVILEGE_FORM: RolePrivilegeFormState = {
  scope: 'SYSTEM',
  privilege: PRIVILEGE_OPTIONS.SYSTEM[0],
  selectorMode: 'specific',
  catalog: '',
  database: '',
  objectName: '',
  withGrantOption: false,
}

function parseSessionProperties(value: string) {
  const sessionProperties: Record<string, string> = {}
  const clearKeys: string[] = []

  for (const rawLine of value.split('\n')) {
    const line = rawLine.trim()
    if (!line) continue
    const [keyPart, ...rest] = line.split('=')
    const key = keyPart.trim()
    if (!key) continue
    const normalizedKey = key.startsWith('session.') ? key : `session.${key}`
    const parsedValue = rest.join('=').trim()
    if (parsedValue) {
      sessionProperties[normalizedKey] = parsedValue
    } else {
      clearKeys.push(normalizedKey)
    }
  }

  return { sessionProperties, clearKeys }
}

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

function detailGrantLabel(grantable: string | null | undefined) {
  return String(grantable).toUpperCase() === 'YES' ? 'Grantable' : 'Granted'
}

function objectDescriptor(privilege: RolePrivilege) {
  const scope = privilege.OBJECT_TYPE ?? 'SYSTEM'
  if (scope === 'SYSTEM') return 'Cluster-wide'
  if (scope === 'CATALOG') return privilege.OBJECT_CATALOG ?? 'Catalog'
  if (scope === 'DATABASE') return privilege.OBJECT_DATABASE ?? 'All databases'

  const database = privilege.OBJECT_DATABASE
  const objectName = privilege.OBJECT_NAME
  if (database && objectName) return `${database}.${objectName}`
  if (database) return `All in ${database}`
  return 'All databases'
}

function SectionCard({
  title,
  description,
  children,
  actions,
}: {
  title: string
  description?: string
  children: React.ReactNode
  actions?: React.ReactNode
}) {
  return (
    <section className='rounded-xl border bg-card/70'>
      <div className='flex flex-wrap items-start justify-between gap-3 border-b px-5 py-4'>
        <div className='space-y-1'>
          <h3 className='text-sm font-semibold tracking-tight'>{title}</h3>
          {description ? (
            <p className='text-sm text-muted-foreground'>{description}</p>
          ) : null}
        </div>
        {actions}
      </div>
      <div className='p-5'>{children}</div>
    </section>
  )
}

function InlineCheckboxList({
  options,
  values,
  onToggle,
  emptyLabel,
}: {
  options: string[]
  values: string[]
  onToggle: (value: string) => void
  emptyLabel: string
}) {
  if (options.length === 0) {
    return <div className='rounded-lg border border-dashed px-3 py-4 text-sm text-muted-foreground'>{emptyLabel}</div>
  }

  return (
    <ScrollArea className='h-36 rounded-lg border'>
      <div className='space-y-2 p-3'>
        {options.map((option) => {
          const checked = values.includes(option)
          return (
            <label key={option} className='flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 hover:bg-muted/50'>
              <Checkbox checked={checked} onCheckedChange={() => onToggle(option)} />
              <span className='text-sm'>{option}</span>
            </label>
          )
        })}
      </div>
    </ScrollArea>
  )
}

function Pagination({
  page,
  pageCount,
  onPrevious,
  onNext,
}: {
  page: number
  pageCount: number
  onPrevious: () => void
  onNext: () => void
}) {
  return (
    <div className='flex items-center justify-between gap-3 border-t px-5 py-3 text-sm text-muted-foreground'>
      <span>
        Page {pageCount === 0 ? 0 : page} of {pageCount}
      </span>
      <div className='flex items-center gap-2'>
        <Button variant='outline' size='sm' onClick={onPrevious} disabled={page <= 1}>
          <ChevronLeft className='size-4' />
          Prev
        </Button>
        <Button variant='outline' size='sm' onClick={onNext} disabled={pageCount === 0 || page >= pageCount}>
          Next
          <ChevronRight className='size-4' />
        </Button>
      </div>
    </div>
  )
}

export function Users() {
  const [activeTab, setActiveTab] = useState<'users' | 'roles'>('users')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [users, setUsers] = useState<AdminUser[]>([])
  const [roles, setRoles] = useState<RoleSummary[]>([])
  const [databases, setDatabases] = useState<string[]>([])
  const [searchUsers, setSearchUsers] = useState('')
  const [searchRoles, setSearchRoles] = useState('')
  const [usersPage, setUsersPage] = useState(1)
  const [rolesPage, setRolesPage] = useState(1)
  const [createUserOpen, setCreateUserOpen] = useState(false)
  const [createRoleOpen, setCreateRoleOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null)
  const [resetPasswordUser, setResetPasswordUser] = useState<AdminUser | null>(null)
  const [generatedPassword, setGeneratedPassword] = useState('')
  const [passwordResetSubmitting, setPasswordResetSubmitting] = useState(false)
  const [roleActionState, setRoleActionState] = useState<RoleActionState | null>(null)
  const [roleActionSubmitting, setRoleActionSubmitting] = useState(false)
  const [userDetail, setUserDetail] = useState<UserDetail | null>(null)
  const [userDetailLoading] = useState(false)
  const [userForm, setUserForm] = useState<UserFormState>(DEFAULT_USER_FORM)
  const [userSubmitting, setUserSubmitting] = useState(false)
  const [roleFormName, setRoleFormName] = useState('')
  const [roleSubmitting, setRoleSubmitting] = useState(false)
  const [editingRole, setEditingRole] = useState<RoleSummary | null>(null)
  const [roleDetail, setRoleDetail] = useState<RoleDetail | null>(null)
  const [roleDetailLoading, setRoleDetailLoading] = useState(false)
  const [privilegeForm, setPrivilegeForm] = useState<RolePrivilegeFormState>(DEFAULT_PRIVILEGE_FORM)
  const [memberUserIdentity, setMemberUserIdentity] = useState('')
  const [memberRoleName, setMemberRoleName] = useState('')
  const [objectOptions, setObjectOptions] = useState<string[]>([])

  const visibleUsersList = useMemo(
    () => users.filter((user) => user.username.toLowerCase() !== 'root'),
    [users],
  )
  const visibleRolesList = useMemo(
    () => roles.filter((role) => role.name.toLowerCase() !== 'root'),
    [roles],
  )

  const userIdentities = useMemo(
    () =>
      visibleUsersList.map((user) => ({
        label: `${user.username}@${user.host}`,
        value: `${user.username}@@${user.host}`,
      })),
    [visibleUsersList],
  )

  async function loadBaseData(showLoader = true) {
    if (showLoader) setLoading(true)
    else setRefreshing(true)

    try {
      const [usersResponse, rolesResponse, databasesResponse] = await Promise.all([
        api.get<{ users: AdminUser[] }>('/users'),
        api.get<{ roles: RoleSummary[] }>('/users/roles'),
        api.get<{ databases: string[] }>('/users/databases'),
      ])

      setUsers(usersResponse.users ?? [])
      setRoles(rolesResponse.roles ?? [])
      setDatabases(databasesResponse.databases ?? [])
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load administrator data')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    void loadBaseData(true)
  }, [])

  useEffect(() => {
    if (privilegeForm.scope === 'SYSTEM' || privilegeForm.scope === 'DATABASE' || privilegeForm.scope === 'CATALOG') {
      setObjectOptions([])
      return
    }
    if (!privilegeForm.database) {
      setObjectOptions([])
      return
    }

    let isCancelled = false

    async function loadObjects() {
      try {
        if (privilegeForm.scope === 'TABLE') {
          const response = await api.get<{ tables: Array<{ name: string } | string> }>(
            `/objects/databases/${encodeURIComponent(privilegeForm.database)}/tables`,
          )
          if (!isCancelled) {
            const tables = (response.tables ?? []).map((entry) => (typeof entry === 'string' ? entry : entry.name))
            setObjectOptions(tables)
          }
          return
        }

        if (privilegeForm.scope === 'VIEW') {
          const response = await api.get<{ views: Array<{ name: string } | string> }>(
            `/objects/databases/${encodeURIComponent(privilegeForm.database)}/views`,
          )
          if (!isCancelled) {
            const views = (response.views ?? []).map((entry) => (typeof entry === 'string' ? entry : entry.name))
            setObjectOptions(views)
          }
          return
        }

        const response = await api.get<{ materialized_views: Array<{ name: string } | string> }>(
          `/objects/databases/${encodeURIComponent(privilegeForm.database)}/objects?type=materialized_view`,
        )
        if (!isCancelled) {
          const materializedViews = (response.materialized_views ?? []).map((entry) =>
            typeof entry === 'string' ? entry : entry.name,
          )
          setObjectOptions(materializedViews)
        }
      } catch {
        if (!isCancelled) setObjectOptions([])
      }
    }

    void loadObjects()

    return () => {
      isCancelled = true
    }
  }, [privilegeForm.database, privilegeForm.scope])

  const filteredUsers = useMemo(() => {
    const query = searchUsers.trim().toLowerCase()
    if (!query) return visibleUsersList
    return visibleUsersList.filter((user) =>
      [user.username, user.host, user.auth_mode, user.roles.join(' '), user.default_roles.join(' ')]
        .join(' ')
        .toLowerCase()
        .includes(query),
    )
  }, [searchUsers, visibleUsersList])

  const filteredRoles = useMemo(() => {
    const query = searchRoles.trim().toLowerCase()
    if (!query) return visibleRolesList
    return visibleRolesList.filter((role) => role.name.toLowerCase().includes(query))
  }, [searchRoles, visibleRolesList])

  const usersPageCount = Math.ceil(filteredUsers.length / PAGE_SIZE)
  const rolesPageCount = Math.ceil(filteredRoles.length / PAGE_SIZE)
  const usersPageStart = filteredUsers.length === 0 ? 0 : (usersPage - 1) * PAGE_SIZE + 1
  const usersPageEnd = Math.min(usersPage * PAGE_SIZE, filteredUsers.length)

  useEffect(() => {
    setUsersPage((current) => Math.min(Math.max(current, 1), Math.max(usersPageCount, 1)))
  }, [usersPageCount])

  useEffect(() => {
    setRolesPage((current) => Math.min(Math.max(current, 1), Math.max(rolesPageCount, 1)))
  }, [rolesPageCount])

  const visibleUsers = useMemo(() => {
    const start = (usersPage - 1) * PAGE_SIZE
    return filteredUsers.slice(start, start + PAGE_SIZE)
  }, [filteredUsers, usersPage])

  const visibleRoles = useMemo(() => {
    const start = (rolesPage - 1) * PAGE_SIZE
    return filteredRoles.slice(start, start + PAGE_SIZE)
  }, [filteredRoles, rolesPage])

  async function openEditRole(role: RoleSummary) {
    setEditingRole(role)
    setRoleDetail(null)
    setRoleDetailLoading(true)
    setPrivilegeForm(DEFAULT_PRIVILEGE_FORM)
    setMemberUserIdentity('')
    setMemberRoleName('')

    try {
      const detail = await api.get<RoleDetail>(`/users/roles/${encodeURIComponent(role.name)}`)
      setRoleDetail(detail)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load role details')
    } finally {
      setRoleDetailLoading(false)
    }
  }

  async function handleCreateUser() {
    if (!userForm.username.trim() || !userForm.password.trim()) {
      toast.error('Username and password are required')
      return
    }
    if (userForm.password !== userForm.confirmPassword) {
      toast.error('Password confirmation does not match')
      return
    }

    const { sessionProperties } = parseSessionProperties(userForm.sessionProperties)
    const selectedRole = userForm.defaultRole.trim()

    setUserSubmitting(true)
    try {
      await api.post('/users', {
        username: userForm.username.trim(),
        password: userForm.password,
        host: '%',
        granted_roles: selectedRole ? [selectedRole] : [],
        default_role_mode: selectedRole ? 'explicit' : 'none',
        default_roles: selectedRole ? [selectedRole] : [],
        session_properties: sessionProperties,
      })
      toast.success('User created successfully')
      setCreateUserOpen(false)
      setUserForm(DEFAULT_USER_FORM)
      await loadBaseData(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to create user')
    } finally {
      setUserSubmitting(false)
    }
  }

  async function handleUpdateUser() {
    if (!editingUser) return

    const originalRoles = new Set(editingUser.roles)
    const nextRoles = new Set(userForm.grantedRoles)
    const grantedRolesAdd = [...nextRoles].filter((role) => !originalRoles.has(role))
    const grantedRolesRemove = [...originalRoles].filter((role) => !nextRoles.has(role))
    const { sessionProperties, clearKeys } = parseSessionProperties(userForm.sessionProperties)

    setUserSubmitting(true)
    try {
      await api.put(`/users/${encodeURIComponent(editingUser.username)}?host=${encodeURIComponent(editingUser.host)}`, {
        password: userForm.password || undefined,
        granted_roles_add: grantedRolesAdd,
        granted_roles_remove: grantedRolesRemove,
        default_role_mode: userForm.defaultRoleMode,
        default_roles: userForm.defaultRoleMode === 'explicit' ? userForm.defaultRoles : [],
        max_user_connections: userForm.maxUserConnections ? Number(userForm.maxUserConnections) : null,
        catalog: userForm.catalog,
        database: userForm.database,
        session_properties: sessionProperties,
        clear_properties: clearKeys,
      })
      toast.success('User updated successfully')
      setEditingUser(null)
      setUserDetail(null)
      await loadBaseData(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to update user')
    } finally {
      setUserSubmitting(false)
    }
  }

  async function handleDeleteUser(user: AdminUser) {
    if (!window.confirm(`Delete user ${user.username}@${user.host}?`)) return

    try {
      await api.delete(`/users/${encodeURIComponent(user.username)}?host=${encodeURIComponent(user.host)}`)
      toast.success('User deleted')
      await loadBaseData(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to delete user')
    }
  }

  async function handleResetPassword() {
    if (!resetPasswordUser) return

    setPasswordResetSubmitting(true)
    try {
      const response = await api.post<{
        username: string
        host: string
        password: string
        message: string
      }>(
        `/users/${encodeURIComponent(resetPasswordUser.username)}/reset-password?host=${encodeURIComponent(resetPasswordUser.host)}`,
      )
      setGeneratedPassword(response.password)
      toast.success('Password reset successfully')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to reset password')
    } finally {
      setPasswordResetSubmitting(false)
    }
  }

  async function handleRoleActionSubmit() {
    if (!roleActionState || !roleActionState.role) {
      toast.error('Select a role first')
      return
    }

    setRoleActionSubmitting(true)
    try {
      if (roleActionState.mode === 'grant') {
        await api.post(`/users/${encodeURIComponent(roleActionState.user.username)}/roles`, {
          role: roleActionState.role,
          host: roleActionState.user.host,
        })
      } else {
        await api.delete(
          `/users/${encodeURIComponent(roleActionState.user.username)}/roles/${encodeURIComponent(roleActionState.role)}?host=${encodeURIComponent(roleActionState.user.host)}`,
        )
      }

      toast.success(
        roleActionState.mode === 'grant' ? 'Role granted successfully' : 'Role revoked successfully',
      )
      setRoleActionState(null)
      await loadBaseData(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to update role')
    } finally {
      setRoleActionSubmitting(false)
    }
  }

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
      setCreateRoleOpen(false)
      await loadBaseData(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to create role')
    } finally {
      setRoleSubmitting(false)
    }
  }

  async function handleDeleteRole(role: RoleSummary) {
    if (!window.confirm(`Delete role ${role.name}?`)) return

    try {
      await api.delete(`/users/roles/${encodeURIComponent(role.name)}`)
      toast.success('Role deleted')
      setEditingRole(null)
      setRoleDetail(null)
      await loadBaseData(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to delete role')
    }
  }

  async function refreshRoleDetail(roleName: string) {
    const detail = await api.get<RoleDetail>(`/users/roles/${encodeURIComponent(roleName)}`)
    setRoleDetail(detail)
  }

  async function handleGrantRoleMember(memberType: 'user' | 'role') {
    if (!editingRole) return

    try {
      if (memberType === 'user') {
        if (!memberUserIdentity) {
          toast.error('Select a user identity first')
          return
        }
        const [username, host] = memberUserIdentity.split('@@')
        await api.post(`/users/roles/${encodeURIComponent(editingRole.name)}/members`, {
          member_type: 'user',
          member_name: username,
          host,
        })
      } else {
        if (!memberRoleName) {
          toast.error('Select a role first')
          return
        }
        await api.post(`/users/roles/${encodeURIComponent(editingRole.name)}/members`, {
          member_type: 'role',
          member_name: memberRoleName,
        })
      }

      toast.success('Role membership updated')
      await refreshRoleDetail(editingRole.name)
      await loadBaseData(false)
      setMemberUserIdentity('')
      setMemberRoleName('')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to update role membership')
    }
  }

  async function handleRevokeRoleMember(memberType: 'user' | 'role', memberName: string, host = '%') {
    if (!editingRole) return
    try {
      await api.delete(`/users/roles/${encodeURIComponent(editingRole.name)}/members`, {
        member_type: memberType,
        member_name: memberName,
        host,
      })
      toast.success('Role membership revoked')
      await refreshRoleDetail(editingRole.name)
      await loadBaseData(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to revoke role membership')
    }
  }

  async function handleGrantPrivilege() {
    if (!editingRole) return
    try {
      await api.post(`/users/roles/${encodeURIComponent(editingRole.name)}/privileges`, {
        privilege: privilegeForm.privilege,
        scope: privilegeForm.scope,
        selector_mode: privilegeForm.selectorMode,
        catalog: privilegeForm.catalog || null,
        database: privilegeForm.database || null,
        object_name: privilegeForm.objectName || null,
        with_grant_option: privilegeForm.withGrantOption,
      })
      toast.success('Privilege granted')
      await refreshRoleDetail(editingRole.name)
      setPrivilegeForm({
        ...DEFAULT_PRIVILEGE_FORM,
        scope: privilegeForm.scope,
        privilege: PRIVILEGE_OPTIONS[privilegeForm.scope][0],
      })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to grant privilege')
    }
  }

  async function handleRevokePrivilege(privilege: RolePrivilege) {
    if (!editingRole) return
    const scope = (privilege.OBJECT_TYPE ?? 'SYSTEM') as PrivilegeScope
    const objectName = privilege.OBJECT_NAME ?? ''
    const database = privilege.OBJECT_DATABASE ?? ''

    let selectorMode: SelectorMode = 'specific'
    if (scope === 'DATABASE' && !database) selectorMode = 'all_databases'
    if (scope !== 'SYSTEM' && scope !== 'CATALOG' && !objectName && database) selectorMode = 'all_in_database'
    if (scope !== 'SYSTEM' && scope !== 'CATALOG' && !database && !objectName) selectorMode = 'all_databases'

    try {
      await api.delete(`/users/roles/${encodeURIComponent(editingRole.name)}/privileges`, {
        privilege: privilege.PRIVILEGE_TYPE,
        scope,
        selector_mode: selectorMode,
        catalog: privilege.OBJECT_CATALOG ?? null,
        database: database || null,
        object_name: objectName || null,
      })
      toast.success('Privilege revoked')
      await refreshRoleDetail(editingRole.name)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to revoke privilege')
    }
  }

  function toggleRoleSelection(values: string[], role: string) {
    return values.includes(role) ? values.filter((item) => item !== role) : [...values, role]
  }

  const roleSelectionOptions = visibleRolesList.map((role) => role.name)
  const mutableRoleOptions = visibleRolesList
    .filter((role) => role.is_mutable)
    .map((role) => role.name)

  return (
    <>
      <Header fixed>
        <Search className='me-auto' />
      </Header>

      <Main className='flex flex-1 flex-col gap-6'>
        <div className='flex flex-wrap items-end justify-between gap-3'>
          <div className='space-y-1'>
            <div className='flex items-center gap-2 text-sm text-muted-foreground'>
              <Shield className='size-4' />
              Administrator
            </div>
            <h1 className='text-2xl font-bold tracking-tight'>Users & Roles</h1>
            <p className='max-w-3xl text-sm text-muted-foreground'>
              Manage StarRocks identities, role memberships, default roles, and scoped privileges from one admin surface.
            </p>
          </div>
          <Button variant='outline' onClick={() => void loadBaseData(false)} disabled={refreshing || loading}>
            <RefreshCw className={cn('size-4', (refreshing || loading) && 'animate-spin')} />
            Refresh
          </Button>
        </div>

        <Tabs
          value={activeTab}
          onValueChange={(value: string) => setActiveTab(value as 'users' | 'roles')}
          className='flex flex-1 flex-col gap-4'
        >
          <TabsList className='grid w-full max-w-sm grid-cols-2'>
            <TabsTrigger value='users'>Users</TabsTrigger>
            <TabsTrigger value='roles'>Roles</TabsTrigger>
          </TabsList>

          <TabsContent value='users' className='m-0 flex flex-col gap-4'>
            <div className='space-y-6'>
              <div>
                <h3 className='text-lg font-medium'>Users</h3>
                <p className='text-sm text-muted-foreground'>
                  Review StarRocks identities, authentication mode, role ownership, and default access.
                </p>
              </div>

              <div className='flex flex-wrap items-center gap-3'>
                <Input
                  value={searchUsers}
                  onChange={(event) => {
                    setSearchUsers(event.target.value)
                    setUsersPage(1)
                  }}
                  placeholder='Search user, auth, role...'
                  className='max-w-xs'
                />
                <div className='ml-auto flex items-center gap-2 text-sm text-muted-foreground'>
                  <span>{filteredUsers.length} users</span>
                  <Button
                    onClick={() => {
                      setUserForm(DEFAULT_USER_FORM)
                      setCreateUserOpen(true)
                    }}
                  >
                    <Plus className='size-4' />
                    Create User
                  </Button>
                </div>
              </div>

              <div className='relative rounded-md border border-border'>
                {refreshing && !loading ? (
                  <div className='pointer-events-none absolute inset-x-4 top-4 z-10 flex justify-center'>
                    <div className='w-full max-w-xs overflow-hidden rounded-full border border-border bg-background/95 shadow-lg backdrop-blur-sm'>
                      <div className='h-1.5 w-full overflow-hidden bg-muted'>
                        <div className='h-full w-1/3 animate-pulse rounded-full bg-primary' />
                      </div>
                      <div className='px-3 py-2 text-center text-xs font-medium text-foreground'>
                        Refreshing users...
                      </div>
                    </div>
                  </div>
                ) : null}
                <div className='overflow-x-auto'>
                  <table className='w-full'>
                    <thead className='border-b border-border bg-muted/50'>
                      <tr>
                        <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                          Name
                        </th>
                        <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                          Last Login
                        </th>
                        <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                          Default Role
                        </th>
                        <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                          Grant Roles
                        </th>
                        <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                          Actions
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {loading ? (
                        <tr>
                          <td
                            colSpan={4}
                            className='px-4 py-12 text-center text-sm text-muted-foreground'
                          >
                            Loading users...
                          </td>
                        </tr>
                      ) : visibleUsers.length === 0 ? (
                        <tr>
                          <td
                            colSpan={4}
                            className='px-4 py-12 text-center text-sm text-muted-foreground'
                          >
                            No users found
                          </td>
                        </tr>
                      ) : (
                        visibleUsers.map((user) => (
                          <tr
                            key={user.identity}
                            className='border-b border-border transition-colors hover:bg-muted/50'
                          >
                            <td className='px-4 py-3 align-top'>
                              <div className='space-y-1'>
                                <div className='flex flex-wrap items-center gap-2'>
                                  <span className='text-sm font-medium'>{user.username}</span>
                                  {user.is_protected ? (
                                    <Badge className='border-red-500/20 bg-red-500/10 text-red-600 dark:text-red-300'>
                                      Protected
                                    </Badge>
                                  ) : null}
                                </div>
                              </div>
                            </td>
                            <td className='px-4 py-3 align-top'>
                              <div className='text-sm'>{formatLastLogin(user.last_login)}</div>
                            </td>
                            <td className='px-4 py-3 align-top'>
                              {user.default_roles[0] ? (
                                <div className='flex flex-wrap gap-2'>
                                  <Badge variant='secondary'>{user.default_roles[0]}</Badge>
                                  {user.roles.length > 1 ? (
                                    <span className='text-xs text-muted-foreground'>
                                      +{user.roles.length - 1} role lain
                                    </span>
                                  ) : null}
                                </div>
                              ) : (
                                <span className='text-xs text-muted-foreground'>None</span>
                              )}
                            </td>
                            <td className='px-4 py-3 align-top'>
                              {user.roles.length ? (
                                <DropdownMenu>
                                  <DropdownMenuTrigger asChild>
                                    <Button
                                      variant='outline'
                                      size='sm'
                                      className='h-8 rounded-full px-3 text-xs'
                                    >
                                      {user.roles.length} role{user.roles.length > 1 ? 's' : ''}
                                    </Button>
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align='start' className='w-48'>
                                    {user.roles.map((role) => (
                                      <DropdownMenuItem key={`${user.identity}-${role}`} disabled>
                                        {role}
                                      </DropdownMenuItem>
                                    ))}
                                  </DropdownMenuContent>
                                </DropdownMenu>
                              ) : (
                                <span className='text-xs text-muted-foreground'>0 role</span>
                              )}
                            </td>
                            <td className='px-4 py-3 align-top'>
                              <div className='flex justify-end'>
                                <DropdownMenu>
                                  <DropdownMenuTrigger asChild>
                                    <Button variant='ghost' size='icon' className='h-8 w-8'>
                                      <MoreHorizontal className='h-4 w-4' />
                                    </Button>
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align='end' className='w-44'>
                                    <DropdownMenuItem
                                      onSelect={(event) => {
                                        event.preventDefault()
                                        setResetPasswordUser(user)
                                        setGeneratedPassword('')
                                      }}
                                      disabled={user.is_protected}
                                    >
                                      Reset password
                                    </DropdownMenuItem>
                                    <DropdownMenuItem
                                      onSelect={(event) => {
                                        event.preventDefault()
                                        setRoleActionState({
                                          mode: 'grant',
                                          user,
                                          role: '',
                                        })
                                      }}
                                      disabled={user.is_protected}
                                    >
                                      Grant role
                                    </DropdownMenuItem>
                                    <DropdownMenuItem
                                      onSelect={(event) => {
                                        event.preventDefault()
                                        setRoleActionState({
                                          mode: 'revoke',
                                          user,
                                          role: user.roles[0] ?? '',
                                        })
                                      }}
                                      disabled={user.is_protected || user.roles.length === 0}
                                    >
                                      Revoke role
                                    </DropdownMenuItem>
                                    <DropdownMenuItem
                                      variant='destructive'
                                      onSelect={(event) => {
                                        event.preventDefault()
                                        void handleDeleteUser(user)
                                      }}
                                      disabled={user.is_protected}
                                    >
                                      Delete user
                                    </DropdownMenuItem>
                                  </DropdownMenuContent>
                                </DropdownMenu>
                              </div>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className='flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between'>
                <div className='text-sm text-muted-foreground'>
                  Showing {usersPageStart}-{usersPageEnd} of {filteredUsers.length}
                </div>
                <div className='flex flex-col gap-2 sm:flex-row sm:items-center'>
                  <span className='text-sm text-muted-foreground'>
                    Page {usersPage} of {Math.max(usersPageCount, 1)}
                  </span>
                  <div className='flex items-center gap-1'>
                    <Button
                      variant='outline'
                      size='icon'
                      onClick={() => setUsersPage(1)}
                      disabled={usersPage === 1}
                      className='h-8 w-8'
                    >
                      <ChevronsLeft className='h-4 w-4' />
                    </Button>
                    <Button
                      variant='outline'
                      size='icon'
                      onClick={() => setUsersPage((page) => Math.max(page - 1, 1))}
                      disabled={usersPage === 1}
                      className='h-8 w-8'
                    >
                      <ChevronLeft className='h-4 w-4' />
                    </Button>
                    <Button
                      variant='outline'
                      size='icon'
                      onClick={() => setUsersPage((page) => Math.min(page + 1, Math.max(usersPageCount, 1)))}
                      disabled={usersPage >= Math.max(usersPageCount, 1)}
                      className='h-8 w-8'
                    >
                      <ChevronRight className='h-4 w-4' />
                    </Button>
                    <Button
                      variant='outline'
                      size='icon'
                      onClick={() => setUsersPage(Math.max(usersPageCount, 1))}
                      disabled={usersPage >= Math.max(usersPageCount, 1)}
                      className='h-8 w-8'
                    >
                      <ChevronsRight className='h-4 w-4' />
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          </TabsContent>

          <TabsContent value='roles' className='m-0 flex flex-col gap-4'>
            <SectionCard
              title='Roles'
              description='Manage custom StarRocks roles, nested role membership, and scoped privilege grants.'
              actions={
                <div className='flex flex-wrap items-center gap-2'>
                  <Input
                    value={searchRoles}
                    onChange={(event) => {
                      setSearchRoles(event.target.value)
                      setRolesPage(1)
                    }}
                    placeholder='Search role...'
                    className='w-72'
                  />
                  <Button onClick={() => setCreateRoleOpen(true)}>
                    <Plus className='size-4' />
                    Create Role
                  </Button>
                </div>
              }
            >
              <div className='overflow-hidden rounded-lg border'>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Role</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Mutability</TableHead>
                      <TableHead className='w-[170px] text-right'>Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {loading ? (
                      <TableRow>
                        <TableCell colSpan={4} className='py-12 text-center text-muted-foreground'>
                          Loading roles...
                        </TableCell>
                      </TableRow>
                    ) : visibleRoles.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={4} className='py-12 text-center text-muted-foreground'>
                          No roles found.
                        </TableCell>
                      </TableRow>
                    ) : (
                      visibleRoles.map((role) => (
                        <TableRow key={role.name}>
                          <TableCell>
                            <div className='flex flex-wrap items-center gap-2'>
                              <span className='font-medium'>{role.name}</span>
                              {role.is_protected ? (
                                <Badge className='border-red-500/20 bg-red-500/10 text-red-600 dark:text-red-300'>
                                  Protected
                                </Badge>
                              ) : null}
                            </div>
                          </TableCell>
                          <TableCell>
                            {role.is_builtin ? (
                              <Badge variant='outline' className='border-amber-500/20 bg-amber-500/10 text-amber-600 dark:text-amber-300'>
                                Built-in
                              </Badge>
                            ) : (
                              <Badge variant='outline' className='border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300'>
                                Custom
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell className='text-sm text-muted-foreground'>
                            {role.is_mutable ? 'Privileges and memberships can be updated' : 'Read only'}
                          </TableCell>
                          <TableCell>
                            <div className='flex justify-end gap-2'>
                              <Button variant='outline' size='sm' onClick={() => void openEditRole(role)}>
                                <KeyRound className='size-4' />
                                Manage
                              </Button>
                              <Button variant='outline' size='sm' onClick={() => void handleDeleteRole(role)} disabled={!role.is_mutable}>
                                <Trash2 className='size-4' />
                                Delete
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
              <Pagination
                page={rolesPage}
                pageCount={rolesPageCount}
                onPrevious={() => setRolesPage((page) => Math.max(page - 1, 1))}
                onNext={() => setRolesPage((page) => Math.min(page + 1, Math.max(rolesPageCount, 1)))}
              />
            </SectionCard>
          </TabsContent>
        </Tabs>
      </Main>

      <Dialog open={createUserOpen} onOpenChange={setCreateUserOpen}>
        <DialogContent className='overflow-hidden border-border bg-[#20252b] p-0 text-white sm:max-w-2xl'>
          <DialogHeader>
            <div className='border-b border-white/10 px-6 py-5 text-center'>
              <DialogTitle className='text-xl font-semibold text-white'>New user</DialogTitle>
              <DialogDescription className='mt-2 text-sm text-white/60'>
                Create a new user
              </DialogDescription>
            </div>
          </DialogHeader>
          <div className='px-6 py-5'>
            <div className='grid gap-5'>
              <div className='grid gap-4 sm:grid-cols-2'>
                <div className='space-y-2'>
                  <Label className='text-white/90'>User name</Label>
                  <Input
                    value={userForm.username}
                    onChange={(event) => setUserForm((current) => ({ ...current, username: event.target.value }))}
                    className='border-white/10 bg-[#1a1e24] text-white'
                  />
                </div>
                <div className='space-y-2'>
                  <Label className='text-white/90'>Default role</Label>
                  <Select
                    value={userForm.defaultRole}
                    onValueChange={(value: string) => setUserForm((current) => ({ ...current, defaultRole: value }))}
                  >
                    <SelectTrigger className='w-full border-white/10 bg-[#1a1e24] text-white'>
                      <SelectValue placeholder='Select role' />
                    </SelectTrigger>
                    <SelectContent>
                      {roleSelectionOptions.map((role) => (
                        <SelectItem key={role} value={role}>
                          {role}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className='grid gap-4 sm:grid-cols-2'>
                <div className='space-y-2'>
                  <Label className='text-white/90'>Password</Label>
                  <Input
                    type='password'
                    value={userForm.password}
                    onChange={(event) => setUserForm((current) => ({ ...current, password: event.target.value }))}
                    className='border-white/10 bg-[#1a1e24] text-white'
                  />
                </div>
                <div className='space-y-2'>
                  <Label className='text-white/90'>Confirm password</Label>
                  <Input
                    type='password'
                    value={userForm.confirmPassword}
                    onChange={(event) => setUserForm((current) => ({ ...current, confirmPassword: event.target.value }))}
                    className='border-white/10 bg-[#1a1e24] text-white'
                  />
                </div>
              </div>

            </div>
          </div>
          <DialogFooter className='border-t border-white/10 bg-[#20252b] px-6 py-4 sm:justify-end'>
            <Button
              variant='outline'
              onClick={() => setCreateUserOpen(false)}
              className='border-white/10 bg-transparent text-white hover:bg-white/5 hover:text-white'
            >
              Cancel
            </Button>
            <Button
              onClick={() => void handleCreateUser()}
              disabled={userSubmitting}
              className='bg-blue-600 text-white hover:bg-blue-500'
            >
              {userSubmitting ? 'Creating...' : 'Create User'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(resetPasswordUser)}
        onOpenChange={(open) => {
          if (!open) {
            setResetPasswordUser(null)
            setGeneratedPassword('')
          }
        }}
      >
        <DialogContent className='border-border bg-[#20252b] text-white sm:max-w-lg'>
          <DialogHeader>
            <DialogTitle className='text-white'>Reset Password</DialogTitle>
            <DialogDescription className='text-white/60'>
              Confirm password reset for {resetPasswordUser?.username}. A new password will be generated and shown once.
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-4'>
            <div className='rounded-lg border border-white/10 bg-[#1a1e24] px-4 py-3 text-sm text-white/80'>
              This action will replace the user password immediately.
            </div>
            {generatedPassword ? (
              <div className='space-y-2'>
                <Label className='text-white/90'>Generated password</Label>
                <div className='rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 font-mono text-sm text-emerald-100'>
                  {generatedPassword}
                </div>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              variant='outline'
              onClick={() => {
                setResetPasswordUser(null)
                setGeneratedPassword('')
              }}
              className='border-white/10 bg-transparent text-white hover:bg-white/5 hover:text-white'
            >
              Close
            </Button>
            <Button
              onClick={() => void handleResetPassword()}
              disabled={passwordResetSubmitting || Boolean(generatedPassword)}
              className='bg-blue-600 text-white hover:bg-blue-500'
            >
              {passwordResetSubmitting ? 'Resetting...' : 'Reset Password'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(roleActionState)}
        onOpenChange={(open) => {
          if (!open) setRoleActionState(null)
        }}
      >
        <DialogContent className='border-border bg-[#20252b] p-0 text-white sm:max-w-xl'>
          <DialogHeader>
            <div className='border-b border-white/10 px-6 py-5 text-center'>
              <DialogTitle className='text-xl font-semibold text-white'>
                {roleActionState?.mode === 'grant' ? 'Grant User a Role' : 'Revoke User Role'}
              </DialogTitle>
              <DialogDescription className='mt-2 text-sm text-white/60'>
                {roleActionState?.mode === 'grant'
                  ? 'Grant one role at a time to the selected user.'
                  : 'Revoke one granted role at a time from the selected user.'}
              </DialogDescription>
            </div>
          </DialogHeader>
          <div className='space-y-5 px-6 py-5'>
            <div className='space-y-2'>
              <Label className='text-white/90'>
                {roleActionState?.mode === 'grant' ? 'User to receive grant' : 'User to revoke'}
              </Label>
              <Input
                value={roleActionState ? roleActionState.user.username : ''}
                readOnly
                className='border-white/10 bg-[#1a1e24] text-white'
              />
            </div>
            <div className='space-y-2'>
              <Label className='text-white/90'>
                {roleActionState?.mode === 'grant' ? 'Role to grant' : 'Role to revoke'}
              </Label>
              <Select
                value={roleActionState?.role ?? ''}
                onValueChange={(value: string) =>
                  setRoleActionState((current) => (current ? { ...current, role: value } : current))
                }
              >
                <SelectTrigger className='w-full border-white/10 bg-[#1a1e24] text-white'>
                  <SelectValue
                    placeholder={
                      roleActionState?.mode === 'grant' ? 'Select a role' : 'Select a granted role'
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {(roleActionState?.mode === 'grant'
                    ? roleSelectionOptions.filter((role) => !roleActionState.user.roles.includes(role))
                    : roleActionState?.user.roles ?? []
                  ).map((role) => (
                    <SelectItem key={role} value={role}>
                      {role}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter className='border-t border-white/10 bg-[#20252b] px-6 py-4 sm:justify-end'>
            <Button
              variant='outline'
              onClick={() => setRoleActionState(null)}
              className='border-white/10 bg-transparent text-white hover:bg-white/5 hover:text-white'
            >
              Cancel
            </Button>
            <Button
              onClick={() => void handleRoleActionSubmit()}
              disabled={roleActionSubmitting || !roleActionState?.role}
              className='bg-blue-600 text-white hover:bg-blue-500'
            >
              {roleActionSubmitting
                ? roleActionState?.mode === 'grant'
                  ? 'Granting...'
                  : 'Revoking...'
                : roleActionState?.mode === 'grant'
                  ? 'Grant'
                  : 'Revoke'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Sheet open={Boolean(editingUser)} onOpenChange={(open) => !open && setEditingUser(null)}>
        <SheetContent className='w-full sm:max-w-2xl'>
          <SheetHeader>
            <SheetTitle>Edit User</SheetTitle>
            <SheetDescription>
              Update password, role assignments, default roles, and supported user properties.
            </SheetDescription>
          </SheetHeader>
          <ScrollArea className='flex-1 px-4'>
            {editingUser ? (
              <div className='space-y-5 pb-6'>
                <div className='rounded-xl border bg-muted/30 p-4'>
                  <div className='flex flex-wrap items-center gap-2'>
                    <span className='font-semibold'>{editingUser.username}</span>
                    <Badge variant='outline'>{editingUser.identity}</Badge>
                    {editingUser.is_protected ? (
                      <Badge className='border-red-500/20 bg-red-500/10 text-red-600 dark:text-red-300'>Protected user</Badge>
                    ) : null}
                  </div>
                  <p className='mt-2 text-sm text-muted-foreground'>
                    Protected users are kept read only. For `root`, password handling should remain external and deliberate.
                  </p>
                </div>

                <div className='grid gap-4 md:grid-cols-2'>
                  <div className='rounded-xl border p-4'>
                    <div className='text-sm font-medium'>Authentication</div>
                    {userDetailLoading ? (
                      <div className='mt-2 text-sm text-muted-foreground'>Loading authentication...</div>
                    ) : (
                      <div className='mt-2 space-y-2 text-sm text-muted-foreground'>
                        <div>Mode: {userDetail?.authentication.auth_mode ?? editingUser.auth_mode}</div>
                        <div>Plugin: {userDetail?.authentication.auth_plugin ?? editingUser.auth_plugin ?? 'native_password'}</div>
                        <div>{(userDetail?.authentication.password_enabled ?? editingUser.password_enabled) ? 'Password enabled' : 'Password disabled'}</div>
                      </div>
                    )}
                  </div>
                  <div className='rounded-xl border p-4'>
                    <div className='text-sm font-medium'>Granted SQL</div>
                    <div className='mt-2 max-h-32 overflow-auto text-xs text-muted-foreground'>
                      {userDetail?.grants?.length ? (
                        <div className='space-y-2'>
                          {userDetail.grants.map((grant) => (
                            <div key={grant} className='rounded-md bg-muted/40 px-2 py-1.5'>
                              {grant}
                            </div>
                          ))}
                        </div>
                      ) : (
                        'No grant statements loaded'
                      )}
                    </div>
                  </div>
                </div>

                <div className='space-y-4'>
                  <div className='space-y-2'>
                    <Label>Reset Password</Label>
                    <Input
                      type='password'
                      value={userForm.password}
                      onChange={(event) => setUserForm((current) => ({ ...current, password: event.target.value }))}
                      placeholder='Leave empty to keep current password'
                      disabled={editingUser.is_protected}
                    />
                  </div>

                  <div className='grid gap-4 lg:grid-cols-2'>
                    <div className='space-y-2'>
                      <Label>Granted Roles</Label>
                      <InlineCheckboxList
                        options={roleSelectionOptions}
                        values={userForm.grantedRoles}
                        onToggle={(role) => setUserForm((current) => ({ ...current, grantedRoles: toggleRoleSelection(current.grantedRoles, role) }))}
                        emptyLabel='No roles available'
                      />
                    </div>
                    <div className='space-y-2'>
                      <Label>Default Role Mode</Label>
                      <Select
                        value={userForm.defaultRoleMode}
                        onValueChange={(value: string) =>
                          setUserForm((current) => ({
                            ...current,
                            defaultRoleMode: value as DefaultRoleMode,
                            defaultRoles: value === 'explicit' ? current.defaultRoles : [],
                          }))
                        }
                        disabled={editingUser.is_protected}
                      >
                        <SelectTrigger className='w-full'>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value='none'>NONE</SelectItem>
                          <SelectItem value='all'>ALL</SelectItem>
                          <SelectItem value='explicit'>Explicit</SelectItem>
                        </SelectContent>
                      </Select>
                      <Label className='pt-2'>Default Roles</Label>
                      <InlineCheckboxList
                        options={roleSelectionOptions}
                        values={userForm.defaultRoles}
                        onToggle={(role) => setUserForm((current) => ({ ...current, defaultRoles: toggleRoleSelection(current.defaultRoles, role) }))}
                        emptyLabel='No roles available'
                      />
                    </div>
                  </div>

                  <div className='grid gap-4 sm:grid-cols-3'>
                    <div className='space-y-2'>
                      <Label>Max User Connections</Label>
                      <Input
                        type='number'
                        value={userForm.maxUserConnections}
                        onChange={(event) => setUserForm((current) => ({ ...current, maxUserConnections: event.target.value }))}
                        disabled={editingUser.is_protected}
                      />
                    </div>
                    <div className='space-y-2'>
                      <Label>Catalog</Label>
                      <Input
                        value={userForm.catalog}
                        onChange={(event) => setUserForm((current) => ({ ...current, catalog: event.target.value }))}
                        disabled={editingUser.is_protected}
                      />
                    </div>
                    <div className='space-y-2'>
                      <Label>Database</Label>
                      <Input
                        value={userForm.database}
                        onChange={(event) => setUserForm((current) => ({ ...current, database: event.target.value }))}
                        disabled={editingUser.is_protected}
                      />
                    </div>
                  </div>

                  <div className='space-y-2'>
                    <Label>Session Properties</Label>
                    <Textarea
                      value={userForm.sessionProperties}
                      onChange={(event) => setUserForm((current) => ({ ...current, sessionProperties: event.target.value }))}
                      rows={7}
                      disabled={editingUser.is_protected}
                    />
                  </div>
                </div>
              </div>
            ) : null}
          </ScrollArea>
          <SheetFooter>
            <Button variant='outline' onClick={() => setEditingUser(null)}>
              Close
            </Button>
            <Button onClick={() => void handleUpdateUser()} disabled={userSubmitting || Boolean(editingUser?.is_protected)}>
              {userSubmitting ? 'Saving...' : 'Save Changes'}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <Dialog open={createRoleOpen} onOpenChange={setCreateRoleOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Role</DialogTitle>
            <DialogDescription>StarRocks roles are created with a single role name, then configured through privileges and memberships.</DialogDescription>
          </DialogHeader>
          <div className='space-y-2'>
            <Label>Role Name</Label>
            <Input value={roleFormName} onChange={(event) => setRoleFormName(event.target.value)} placeholder='analyst' />
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setCreateRoleOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void handleCreateRole()} disabled={roleSubmitting}>
              {roleSubmitting ? 'Creating...' : 'Create Role'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Sheet open={Boolean(editingRole)} onOpenChange={(open) => !open && setEditingRole(null)}>
        <SheetContent className='w-full sm:max-w-4xl'>
          <SheetHeader>
            <SheetTitle>Manage Role</SheetTitle>
            <SheetDescription>
              Update memberships and privileges. Role rename is intentionally not supported because StarRocks does not expose a true role rename workflow.
            </SheetDescription>
          </SheetHeader>
          <ScrollArea className='flex-1 px-4'>
            {editingRole ? (
              <div className='space-y-5 pb-6'>
                <div className='rounded-xl border bg-muted/30 p-4'>
                  <div className='flex flex-wrap items-center gap-2'>
                    <span className='font-semibold'>{editingRole.name}</span>
                    {editingRole.is_builtin ? (
                      <Badge className='border-amber-500/20 bg-amber-500/10 text-amber-600 dark:text-amber-300'>Built-in</Badge>
                    ) : null}
                    {editingRole.is_protected ? (
                      <Badge className='border-red-500/20 bg-red-500/10 text-red-600 dark:text-red-300'>Protected</Badge>
                    ) : null}
                    {!editingRole.is_mutable ? (
                      <Badge variant='outline'>Read only</Badge>
                    ) : null}
                  </div>
                  <p className='mt-2 text-sm text-muted-foreground'>
                    Built-in roles and `ACCOUNTADMIN` stay protected. Custom roles can be extended through grants and memberships.
                  </p>
                </div>

                {roleDetailLoading ? (
                  <div className='rounded-xl border px-4 py-8 text-sm text-muted-foreground'>Loading role detail...</div>
                ) : roleDetail ? (
                  <>
                    <div className='grid gap-4 xl:grid-cols-[1.1fr_0.9fr]'>
                      <div className='space-y-4'>
                        <div className='rounded-xl border p-4'>
                          <div className='flex items-center gap-2 text-sm font-medium'>
                            <UsersIcon className='size-4' />
                            User Members
                          </div>
                          <div className='mt-3 flex flex-wrap gap-2'>
                            {roleDetail.members.users.length ? (
                              roleDetail.members.users.map((member) => (
                                <div key={member.identity} className='flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm'>
                                  <span>{member.username}@{member.host}</span>
                                  {editingRole.is_mutable ? (
                                    <button
                                      type='button'
                                      className='text-muted-foreground transition-colors hover:text-foreground'
                                      onClick={() => void handleRevokeRoleMember('user', member.username, member.host)}
                                    >
                                      Remove
                                    </button>
                                  ) : null}
                                </div>
                              ))
                            ) : (
                              <div className='text-sm text-muted-foreground'>No user members yet.</div>
                            )}
                          </div>
                          <div className='mt-4 flex flex-col gap-2 sm:flex-row'>
                            <Select value={memberUserIdentity} onValueChange={setMemberUserIdentity} disabled={!editingRole.is_mutable}>
                              <SelectTrigger className='w-full'>
                                <SelectValue placeholder='Select user identity' />
                              </SelectTrigger>
                              <SelectContent>
                                {userIdentities.map((identity) => (
                                  <SelectItem key={identity.value} value={identity.value}>
                                    {identity.label}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                            <Button onClick={() => void handleGrantRoleMember('user')} disabled={!editingRole.is_mutable}>
                              Add User
                            </Button>
                          </div>
                        </div>

                        <div className='rounded-xl border p-4'>
                          <div className='flex items-center gap-2 text-sm font-medium'>
                            <Shield className='size-4' />
                            Nested Roles
                          </div>
                          <div className='mt-3 flex flex-wrap gap-2'>
                            {roleDetail.members.nested_roles.length ? (
                              roleDetail.members.nested_roles.map((memberRole) => (
                                <div key={memberRole} className='flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm'>
                                  <span>{memberRole}</span>
                                  {editingRole.is_mutable ? (
                                    <button
                                      type='button'
                                      className='text-muted-foreground transition-colors hover:text-foreground'
                                      onClick={() => void handleRevokeRoleMember('role', memberRole)}
                                    >
                                      Remove
                                    </button>
                                  ) : null}
                                </div>
                              ))
                            ) : (
                              <div className='text-sm text-muted-foreground'>No nested roles yet.</div>
                            )}
                          </div>
                          <div className='mt-4 flex flex-col gap-2 sm:flex-row'>
                            <Select value={memberRoleName} onValueChange={setMemberRoleName} disabled={!editingRole.is_mutable}>
                              <SelectTrigger className='w-full'>
                                <SelectValue placeholder='Select role to grant' />
                              </SelectTrigger>
                              <SelectContent>
                                {mutableRoleOptions
                                  .filter((roleName) => roleName !== editingRole.name)
                                  .map((roleName) => (
                                    <SelectItem key={roleName} value={roleName}>
                                      {roleName}
                                    </SelectItem>
                                  ))}
                              </SelectContent>
                            </Select>
                            <Button onClick={() => void handleGrantRoleMember('role')} disabled={!editingRole.is_mutable}>
                              Add Role
                            </Button>
                          </div>
                          {roleDetail.members.parent_roles.length ? (
                            <>
                              <Separator className='my-4' />
                              <div className='space-y-2'>
                                <div className='text-sm font-medium'>Granted Into Roles</div>
                                <div className='flex flex-wrap gap-2'>
                                  {roleDetail.members.parent_roles.map((parentRole) => (
                                    <Badge key={parentRole} variant='secondary'>
                                      {parentRole}
                                    </Badge>
                                  ))}
                                </div>
                              </div>
                            </>
                          ) : null}
                        </div>
                      </div>

                      <div className='rounded-xl border p-4'>
                        <div className='flex items-center gap-2 text-sm font-medium'>
                          <Database className='size-4' />
                          Add Privilege
                        </div>
                        <div className='mt-4 space-y-3'>
                          <div className='space-y-2'>
                            <Label>Scope</Label>
                            <Select
                              value={privilegeForm.scope}
                              onValueChange={(value: string) =>
                                setPrivilegeForm((current) => ({
                                  ...current,
                                  scope: value as PrivilegeScope,
                                  privilege: PRIVILEGE_OPTIONS[value as PrivilegeScope][0],
                                  selectorMode:
                                    value === 'SYSTEM' || value === 'CATALOG'
                                      ? 'specific'
                                      : current.selectorMode,
                                  objectName: '',
                                }))
                              }
                              disabled={!editingRole.is_mutable}
                            >
                              <SelectTrigger className='w-full'>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {(Object.keys(PRIVILEGE_OPTIONS) as PrivilegeScope[]).map((scope) => (
                                  <SelectItem key={scope} value={scope}>
                                    {scope}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>

                          <div className='space-y-2'>
                            <Label>Privilege</Label>
                            <Select
                              value={privilegeForm.privilege}
                              onValueChange={(value: string) =>
                                setPrivilegeForm((current) => ({ ...current, privilege: value }))
                              }
                              disabled={!editingRole.is_mutable}
                            >
                              <SelectTrigger className='w-full'>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {PRIVILEGE_OPTIONS[privilegeForm.scope].map((privilege) => (
                                  <SelectItem key={privilege} value={privilege}>
                                    {privilege}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>

                          {privilegeForm.scope !== 'SYSTEM' && privilegeForm.scope !== 'CATALOG' ? (
                            <div className='space-y-2'>
                              <Label>Selector Mode</Label>
                              <Select
                                value={privilegeForm.selectorMode}
                                onValueChange={(value: string) =>
                                  setPrivilegeForm((current) => ({
                                    ...current,
                                    selectorMode: value as SelectorMode,
                                    objectName: value === 'specific' ? current.objectName : '',
                                  }))
                                }
                                disabled={!editingRole.is_mutable}
                              >
                                <SelectTrigger className='w-full'>
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value='specific'>Specific</SelectItem>
                                  <SelectItem value='all_in_database'>All in database</SelectItem>
                                  <SelectItem value='all_databases'>All databases</SelectItem>
                                </SelectContent>
                              </Select>
                            </div>
                          ) : null}

                          {privilegeForm.scope === 'CATALOG' ? (
                            <div className='space-y-2'>
                              <Label>Catalog</Label>
                              <Input
                                value={privilegeForm.catalog}
                                onChange={(event) => setPrivilegeForm((current) => ({ ...current, catalog: event.target.value }))}
                                disabled={!editingRole.is_mutable}
                              />
                            </div>
                          ) : null}

                          {privilegeForm.scope !== 'SYSTEM' && privilegeForm.scope !== 'CATALOG' && privilegeForm.selectorMode !== 'all_databases' ? (
                            <div className='space-y-2'>
                              <Label>Database</Label>
                              <Select
                                value={privilegeForm.database}
                                onValueChange={(value: string) =>
                                  setPrivilegeForm((current) => ({ ...current, database: value, objectName: '' }))
                                }
                                disabled={!editingRole.is_mutable}
                              >
                                <SelectTrigger className='w-full'>
                                  <SelectValue placeholder='Select database' />
                                </SelectTrigger>
                                <SelectContent>
                                  {databases.map((database) => (
                                    <SelectItem key={database} value={database}>
                                      {database}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>
                          ) : null}

                          {privilegeForm.scope !== 'SYSTEM' &&
                          privilegeForm.scope !== 'CATALOG' &&
                          privilegeForm.scope !== 'DATABASE' &&
                          privilegeForm.selectorMode === 'specific' ? (
                            <div className='space-y-2'>
                              <Label>Object</Label>
                              <Select
                                value={privilegeForm.objectName}
                                onValueChange={(value: string) =>
                                  setPrivilegeForm((current) => ({ ...current, objectName: value }))
                                }
                                disabled={!editingRole.is_mutable}
                              >
                                <SelectTrigger className='w-full'>
                                  <SelectValue placeholder='Select object' />
                                </SelectTrigger>
                                <SelectContent>
                                  {objectOptions.map((objectName) => (
                                    <SelectItem key={objectName} value={objectName}>
                                      {objectName}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>
                          ) : null}

                          <label className='flex items-center gap-3 rounded-lg border px-3 py-2 text-sm'>
                            <Checkbox
                              checked={privilegeForm.withGrantOption}
                              onCheckedChange={(checked) =>
                                setPrivilegeForm((current) => ({ ...current, withGrantOption: checked === true }))
                              }
                              disabled={!editingRole.is_mutable}
                            />
                            <span>With grant option</span>
                          </label>

                          <Button className='w-full' onClick={() => void handleGrantPrivilege()} disabled={!editingRole.is_mutable}>
                            Grant Privilege
                          </Button>
                        </div>
                      </div>
                    </div>

                    <div className='rounded-xl border'>
                      <div className='flex items-center justify-between gap-2 border-b px-4 py-3'>
                        <div>
                          <div className='text-sm font-medium'>Privileges</div>
                          <div className='text-sm text-muted-foreground'>Structured StarRocks grants for supported scopes.</div>
                        </div>
                        <Badge variant='outline'>{roleDetail.privileges.length} grants</Badge>
                      </div>
                      <div className='overflow-x-auto'>
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Privilege</TableHead>
                              <TableHead>Scope</TableHead>
                              <TableHead>Object</TableHead>
                              <TableHead>Grantability</TableHead>
                              <TableHead className='w-[120px] text-right'>Action</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {roleDetail.privileges.length ? (
                              roleDetail.privileges.map((privilege, index) => (
                                <TableRow key={`${privilege.PRIVILEGE_TYPE}-${privilege.OBJECT_TYPE}-${privilege.OBJECT_NAME}-${index}`}>
                                  <TableCell>{privilege.PRIVILEGE_TYPE ?? '-'}</TableCell>
                                  <TableCell>{privilege.OBJECT_TYPE ?? 'SYSTEM'}</TableCell>
                                  <TableCell className='text-sm text-muted-foreground'>{objectDescriptor(privilege)}</TableCell>
                                  <TableCell>
                                    <Badge variant='outline'>{detailGrantLabel(privilege.IS_GRANTABLE)}</Badge>
                                  </TableCell>
                                  <TableCell>
                                    <div className='flex justify-end'>
                                      <Button
                                        variant='outline'
                                        size='sm'
                                        onClick={() => void handleRevokePrivilege(privilege)}
                                        disabled={!editingRole.is_mutable}
                                      >
                                        Revoke
                                      </Button>
                                    </div>
                                  </TableCell>
                                </TableRow>
                              ))
                            ) : (
                              <TableRow>
                                <TableCell colSpan={5} className='py-10 text-center text-muted-foreground'>
                                  No structured privileges found for this role.
                                </TableCell>
                              </TableRow>
                            )}
                          </TableBody>
                        </Table>
                      </div>
                    </div>

                    <div className='rounded-xl border p-4'>
                      <div className='text-sm font-medium'>Raw Grants</div>
                      <div className='mt-3 max-h-48 overflow-auto space-y-2 text-xs text-muted-foreground'>
                        {roleDetail.grants.length ? (
                          roleDetail.grants.map((grant) => (
                            <div key={grant} className='rounded-md bg-muted/40 px-3 py-2'>
                              {grant}
                            </div>
                          ))
                        ) : (
                          <div>No raw grants available.</div>
                        )}
                      </div>
                    </div>
                  </>
                ) : null}
              </div>
            ) : null}
          </ScrollArea>
          <SheetFooter>
            <Button variant='outline' onClick={() => setEditingRole(null)}>
              Close
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  )
}
