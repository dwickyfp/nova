import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Code,
  FolderOpen,
  LogIn,
  Activity,
  ChevronDown,
  ChevronRight,
  Clock,
  User,
  Database,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn, getPageNumbers } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AuditItem {
  log_id: string
  event_type: string
  event_time: string
  user_name: string
  ip_address: string
  object_type: string
  object_name: string
  action: string
  sql_text: string
  status: string
  error_message: string
  duration_ms: number
  rows_affected: number
  session_id: string
  database_name: string
  schema_name: string
}

interface AuditResponse {
  items: AuditItem[]
  total: number
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50

const EVENT_TYPE_OPTIONS = [
  { value: 'all', label: 'All Types' },
  { value: 'query', label: 'Query' },
  { value: 'workspace', label: 'Workspace' },
  { value: 'login', label: 'Login' },
] as const

const STATUS_OPTIONS = [
  { value: 'all', label: 'All Statuses' },
  { value: 'SUCCESS', label: 'Success' },
  { value: 'ERROR', label: 'Error' },
] as const

const EVENT_ICON: Record<string, React.ElementType> = {
  query: Code,
  workspace: FolderOpen,
  login: LogIn,
}

const EVENT_BADGE_CLASSES: Record<string, string> = {
  query: 'bg-chart-1/10 text-chart-1 border-chart-1/20',
  workspace: 'bg-purple-500/10 text-purple-600 border-purple-500/20 dark:text-purple-400',
  login: 'bg-chart-2/10 text-chart-2 border-chart-2/20',
}

const DEFAULT_BADGE_CLASS =
  'bg-muted text-muted-foreground border-border'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildQueryString(params: Record<string, string | number>) {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== '' && v !== 'all') qs.set(k, String(v))
  }
  return qs.toString()
}

function formatTime(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatRelative(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

function getEventIcon(eventType: string) {
  return EVENT_ICON[eventType] ?? Activity
}

function getBadgeClass(eventType: string) {
  return EVENT_BADGE_CLASSES[eventType] ?? DEFAULT_BADGE_CLASS
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusDot({ status }: { status: string }) {
  const isError = status === 'ERROR'
  return (
    <span
      className={cn(
        'inline-block size-2 rounded-full',
        isError ? 'bg-destructive' : 'bg-chart-2'
      )}
      aria-label={isError ? 'Error' : 'Success'}
    />
  )
}

function FilterBar({
  eventType,
  setEventType,
  status,
  setStatus,
  userName,
  setUserName,
  users,
}: {
  eventType: string
  setEventType: (v: string) => void
  status: string
  setStatus: (v: string) => void
  userName: string
  setUserName: (v: string) => void
  users: string[]
}) {
  return (
    <div className='flex flex-wrap items-center gap-2'>
      <Select value={eventType} onValueChange={setEventType}>
        <SelectTrigger size='sm'>
          <SelectValue placeholder='Event Type' />
        </SelectTrigger>
        <SelectContent>
          {EVENT_TYPE_OPTIONS.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={status} onValueChange={setStatus}>
        <SelectTrigger size='sm'>
          <SelectValue placeholder='Status' />
        </SelectTrigger>
        <SelectContent>
          {STATUS_OPTIONS.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={userName} onValueChange={setUserName}>
        <SelectTrigger size='sm'>
          <SelectValue placeholder='All Users' />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value='all'>All Users</SelectItem>
          {users.map((u) => (
            <SelectItem key={u} value={u}>
              {u}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function AuditCard({ item }: { item: AuditItem }) {
  const [expanded, setExpanded] = useState(false)
  const Icon = getEventIcon(item.event_type)
  const badgeClass = getBadgeClass(item.event_type)

  return (
    <div className='relative flex gap-4'>
      {/* Timeline rail */}
      <div className='flex flex-col items-center'>
        <div
          className={cn(
            'flex size-9 shrink-0 items-center justify-center rounded-full border',
            item.status === 'ERROR'
              ? 'border-destructive/30 bg-destructive/10 text-destructive'
              : 'border-border bg-muted text-muted-foreground'
          )}
        >
          <Icon className='size-4' />
        </div>
        <div className='mt-2 w-px flex-1 bg-border' />
      </div>

      {/* Card body */}
      <button
        type='button'
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          'mb-4 flex-1 rounded-lg border bg-card p-4 text-start shadow-xs transition-colors hover:bg-accent/50',
          expanded && 'ring-1 ring-ring/30'
        )}
      >
        {/* Header row */}
        <div className='flex flex-wrap items-center gap-2'>
          {expanded ? (
            <ChevronDown className='size-4 text-muted-foreground' />
          ) : (
            <ChevronRight className='size-4 text-muted-foreground' />
          )}

          <Badge
            variant='outline'
            className={cn('text-[11px]', badgeClass)}
          >
            {item.event_type}
          </Badge>

          <StatusDot status={item.status} />

          <span className='text-sm font-medium text-foreground'>
            {item.action}
          </span>

          {item.object_name && (
            <span className='truncate text-sm text-muted-foreground'>
              {item.object_type} &ldquo;{item.object_name}&rdquo;
            </span>
          )}

          <span className='ms-auto flex items-center gap-1 text-xs text-muted-foreground'>
            <Clock className='size-3' />
            <span title={formatTime(item.event_time)}>
              {formatRelative(item.event_time)}
            </span>
          </span>
        </div>

        {/* Meta row */}
        <div className='mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground'>
          <span className='flex items-center gap-1'>
            <User className='size-3' />
            {item.user_name || '—'}
          </span>
          {item.database_name && (
            <span className='flex items-center gap-1'>
              <Database className='size-3' />
              {item.database_name}
              {item.schema_name ? `.${item.schema_name}` : ''}
            </span>
          )}
          {item.duration_ms != null && (
            <span>{item.duration_ms} ms</span>
          )}
          {item.rows_affected != null && (
            <span>{item.rows_affected.toLocaleString()} rows</span>
          )}
          {item.ip_address && <span>{item.ip_address}</span>}
        </div>

        {/* Expanded details */}
        {expanded && (
          <div className='mt-4 space-y-3 border-t pt-3 text-sm'>
            {item.sql_text && (
              <DetailBlock label='SQL Text'>
                <pre className='overflow-x-auto whitespace-pre-wrap rounded bg-muted p-2 font-mono text-xs text-foreground'>
                  {item.sql_text}
                </pre>
              </DetailBlock>
            )}

            {item.error_message && (
              <DetailBlock label='Error'>
                <pre className='overflow-x-auto whitespace-pre-wrap rounded bg-destructive/10 p-2 font-mono text-xs text-destructive'>
                  {item.error_message}
                </pre>
              </DetailBlock>
            )}

            <div className='grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-3'>
              <DetailField label='Status' value={item.status} />
              <DetailField label='Session ID' value={item.session_id} />
              <DetailField label='Log ID' value={item.log_id} />
              <DetailField label='Object Type' value={item.object_type} />
              <DetailField label='Object Name' value={item.object_name} />
              <DetailField label='IP Address' value={item.ip_address} />
              <DetailField label='Database' value={item.database_name} />
              <DetailField label='Schema' value={item.schema_name} />
              <DetailField
                label='Event Time'
                value={formatTime(item.event_time)}
              />
            </div>
          </div>
        )}
      </button>
    </div>
  )
}

function DetailBlock({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div>
      <p className='mb-1 text-xs font-medium text-muted-foreground'>
        {label}
      </p>
      {children}
    </div>
  )
}

function DetailField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className='text-muted-foreground'>{label}</p>
      <p className='truncate font-medium text-foreground'>{value || '—'}</p>
    </div>
  )
}

function Pagination({
  page,
  totalPages,
  total,
  onPageChange,
}: {
  page: number
  totalPages: number
  total: number
  onPageChange: (p: number) => void
}) {
  if (totalPages <= 1) return null
  const pages = getPageNumbers(page, totalPages)

  return (
    <div className='flex flex-wrap items-center justify-between gap-2 border-t pt-4'>
      <p className='text-xs text-muted-foreground'>
        Showing {(page - 1) * PAGE_SIZE + 1}–
        {Math.min(page * PAGE_SIZE, total)} of {total.toLocaleString()} events
      </p>
      <div className='flex items-center gap-1'>
        <Button
          variant='outline'
          size='sm'
          disabled={page === 1}
          onClick={() => onPageChange(page - 1)}
        >
          Prev
        </Button>
        {pages.map((p, i) =>
          typeof p === 'string' ? (
            <span
              key={`ellipsis-${i}`}
              className='px-2 text-xs text-muted-foreground'
            >
              …
            </span>
          ) : (
            <Button
              key={p}
              variant={p === page ? 'default' : 'outline'}
              size='sm'
              onClick={() => onPageChange(p)}
            >
              {p}
            </Button>
          )
        )}
        <Button
          variant='outline'
          size='sm'
          disabled={page === totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  )
}

function TimelineSkeleton() {
  return (
    <div className='space-y-4'>
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className='flex gap-4'>
          <Skeleton className='size-9 shrink-0 rounded-full' />
          <div className='flex-1 space-y-2 rounded-lg border p-4'>
            <Skeleton className='h-4 w-3/4' />
            <Skeleton className='h-3 w-1/2' />
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function MonitoringAuditTrail() {
  // Filters
  const [eventType, setEventType] = useState('all')
  const [status, setStatus] = useState('all')
  const [userName, setUserName] = useState('all')
  const [page, setPage] = useState(1)

  // Reset to page 1 when filters change
  const handleSetEventType = (v: string) => {
    setEventType(v)
    setPage(1)
  }
  const handleSetStatus = (v: string) => {
    setStatus(v)
    setPage(1)
  }
  const handleSetUserName = (v: string) => {
    setUserName(v)
    setPage(1)
  }

  const offset = (page - 1) * PAGE_SIZE

  const qs = buildQueryString({
    limit: PAGE_SIZE,
    offset,
    event_type: eventType,
    user_name: userName,
    status,
  })

  const query = useQuery<AuditResponse>({
    queryKey: ['monitoring', 'audit', eventType, status, userName, page],
    queryFn: () => api.get<AuditResponse>(`/monitoring/audit?${qs}`),
  })

  if (query.isError) {
    toast.error(query.error.message || 'Failed to load audit trail')
  }

  const items = query.data?.items ?? []
  const total = query.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  // Derive unique user names from the current result set for the filter
  const users = Array.from(
    new Set(items.map((i) => i.user_name).filter(Boolean))
  ).sort()

  return (
    <div className='space-y-4'>
      {/* Header */}
      <div>
        <h3 className='text-lg font-medium'>Audit Trail</h3>
        <p className='text-sm text-muted-foreground'>
          Track user actions, schema changes, and access events.
        </p>
      </div>

      {/* Filter bar */}
      <FilterBar
        eventType={eventType}
        setEventType={handleSetEventType}
        status={status}
        setStatus={handleSetStatus}
        userName={userName}
        setUserName={handleSetUserName}
        users={users}
      />

      {/* Timeline */}
      {query.isLoading ? (
        <TimelineSkeleton />
      ) : items.length === 0 ? (
        <div className='rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground'>
          No audit events found.
        </div>
      ) : (
        <div>
          {items.map((item) => (
            <AuditCard key={item.log_id} item={item} />
          ))}
          {/* Trailing dot to cap the timeline */}
          <div className='flex justify-start ps-[13px]'>
            <div className='size-2.5 rounded-full bg-border' />
          </div>
        </div>
      )}

      {/* Pagination */}
      {!query.isLoading && total > 0 && (
        <Pagination
          page={page}
          totalPages={totalPages}
          total={total}
          onPageChange={setPage}
        />
      )}
    </div>
  )
}
