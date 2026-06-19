import { Fragment, useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Code,
  Database,
  FolderOpen,
  LogIn,
  User,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { SearchableSelect } from '@/components/ui/searchable-select'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Button } from '@/components/ui/button'

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

const EVENT_TYPE_OPTIONS = ['query', 'workspace', 'login']
const STATUS_OPTIONS = ['SUCCESS', 'ERROR']

const EVENT_ICON: Record<string, React.ElementType> = {
  query: Code,
  workspace: FolderOpen,
  login: LogIn,
}

function buildQueryString(params: Record<string, string | number>) {
  const queryString = new URLSearchParams()

  for (const [key, value] of Object.entries(params)) {
    if (value !== '' && value !== 'all') {
      queryString.set(key, String(value))
    }
  }

  return queryString.toString()
}

function formatTime(iso: string) {
  const date = new Date(iso)
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatDuration(durationMs: number | null | undefined) {
  if (durationMs == null) return '—'
  if (durationMs < 1000) return `${durationMs}ms`
  return `${(durationMs / 1000).toFixed(2)}s`
}

function truncateText(value: string, maxLength: number = 80) {
  if (value.length <= maxLength) return value
  return `${value.slice(0, maxLength).trim()}...`
}

function getEventBadgeClassName(eventType: string) {
  const normalizedType = eventType.toLowerCase()

  if (normalizedType === 'query') {
    return 'border-transparent bg-sky-600 text-white hover:bg-sky-600'
  }

  if (normalizedType === 'workspace') {
    return 'border-transparent bg-violet-600 text-white hover:bg-violet-600'
  }

  if (normalizedType === 'login') {
    return 'border-transparent bg-emerald-600 text-white hover:bg-emerald-600'
  }

  return 'border-transparent bg-slate-600 text-white hover:bg-slate-600'
}

function getStatusBadgeClassName(status: string) {
  const normalizedStatus = status.toUpperCase()

  if (normalizedStatus === 'SUCCESS') {
    return 'border-transparent bg-emerald-600 text-white hover:bg-emerald-600'
  }

  if (
    normalizedStatus === 'FAILED' ||
    normalizedStatus === 'FAILURE' ||
    normalizedStatus === 'ERROR'
  ) {
    return 'border-transparent bg-red-600 text-white hover:bg-red-600'
  }

  return 'border-transparent bg-slate-600 text-white hover:bg-slate-600'
}

export function MonitoringAuditTrail() {
  const [eventType, setEventType] = useState('')
  const [status, setStatus] = useState('')
  const [userName, setUserName] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(5)

  const offset = (page - 1) * pageSize

  const queryString = buildQueryString({
    limit: pageSize,
    offset,
    event_type: eventType,
    user_name: userName,
    status,
  })

  const auditQuery = useQuery<AuditResponse>({
    queryKey: [
      'monitoring',
      'audit',
      eventType,
      status,
      userName,
      page,
      pageSize,
    ],
    queryFn: () => api.get<AuditResponse>(`/monitoring/audit?${queryString}`),
    placeholderData: keepPreviousData,
  })

  useEffect(() => {
    if (auditQuery.error) {
      toast.error(auditQuery.error.message || 'Failed to load audit trail')
    }
  }, [auditQuery.error])

  const queryItems = auditQuery.data?.items ?? []

  const filteredItems = useMemo(() => {
    const normalizedSearchQuery = searchQuery.trim().toLowerCase()

    if (!normalizedSearchQuery) return queryItems

    return queryItems.filter((item) =>
      [
        item.action,
        item.object_name,
        item.object_type,
        item.sql_text,
        item.user_name,
        item.database_name,
        item.schema_name,
        item.ip_address,
      ]
        .filter(Boolean)
        .some((value) => value.toLowerCase().includes(normalizedSearchQuery))
    )
  }, [queryItems, searchQuery])

  const total = auditQuery.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const pageStart = total === 0 ? 0 : (page - 1) * pageSize + 1
  const pageEnd = Math.min(page * pageSize, total)

  const userOptions = useMemo(() => {
    return [...new Set(queryItems.map((item) => item.user_name).filter(Boolean))] as string[]
  }, [queryItems])

  const handleFilterChange = (
    setter: (value: string) => void,
    value: string
  ) => {
    setter(value)
    setPage(1)
    setExpandedRow(null)
  }

  const handlePageChange = (newPage: number) => {
    setPage(newPage)
    setExpandedRow(null)
  }

  const handlePageSizeChange = (value: string) => {
    setPageSize(Number(value))
    setPage(1)
    setExpandedRow(null)
  }

  return (
    <div className='space-y-6'>
      <div>
        <h3 className='text-lg font-medium'>Audit Trail</h3>
        <p className='text-sm text-muted-foreground'>
          Track user actions, schema changes, and access events.
        </p>
      </div>

      <div className='flex flex-wrap items-center gap-3'>
        <SearchableSelect
          options={EVENT_TYPE_OPTIONS}
          value={eventType}
          onChange={(value: string) => handleFilterChange(setEventType, value)}
          label='Event Type'
          icon={<Activity size={14} />}
        />

        <SearchableSelect
          options={STATUS_OPTIONS}
          value={status}
          onChange={(value: string) => handleFilterChange(setStatus, value)}
          label='Status'
          icon={<CheckCircle2 size={14} />}
        />

        <SearchableSelect
          options={userOptions}
          value={userName}
          onChange={(value: string) => handleFilterChange(setUserName, value)}
          label='User'
          icon={<User size={14} />}
        />

        <Input
          placeholder='Search action, object, SQL...'
          value={searchQuery}
          onChange={(event) => {
            setSearchQuery(event.target.value)
            setExpandedRow(null)
          }}
          className='max-w-xs'
        />

        <div className='ml-auto text-sm text-muted-foreground'>
          {total} {total === 1 ? 'event' : 'events'}
        </div>
      </div>

      <div className='relative rounded-md border border-border'>
        {auditQuery.isFetching && !auditQuery.isLoading ? (
          <div className='pointer-events-none absolute inset-x-4 top-4 z-10 flex justify-center'>
            <div className='w-full max-w-xs overflow-hidden rounded-full border border-border bg-background/95 shadow-lg backdrop-blur-sm'>
              <div className='h-1.5 w-full overflow-hidden bg-muted'>
                <div className='h-full w-1/3 animate-pulse rounded-full bg-primary' />
              </div>
              <div className='px-3 py-2 text-center text-xs font-medium text-foreground'>
                Loading audit events...
              </div>
            </div>
          </div>
        ) : null}

        <div className='overflow-x-auto'>
          <table className='w-full'>
            <thead className='border-b border-border bg-muted/50'>
              <tr>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  Time
                </th>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  User
                </th>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  Event
                </th>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  Action
                </th>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  Status
                </th>
                <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                  Duration
                </th>
                <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                  Rows
                </th>
              </tr>
            </thead>
            <tbody>
              {auditQuery.isLoading ? (
                <tr>
                  <td
                    colSpan={7}
                    className='px-4 py-12 text-center text-sm text-muted-foreground'
                  >
                    Loading...
                  </td>
                </tr>
              ) : filteredItems.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    className='px-4 py-12 text-center text-sm text-muted-foreground'
                  >
                    No audit events found
                  </td>
                </tr>
              ) : (
                filteredItems.map((item) => {
                  const EventIcon = EVENT_ICON[item.event_type] ?? Activity

                  return (
                    <Fragment key={item.log_id}>
                      <tr
                        onClick={() =>
                          setExpandedRow(
                            expandedRow === item.log_id ? null : item.log_id
                          )
                        }
                        className={cn(
                          'cursor-pointer border-b border-border transition-colors hover:bg-muted/50',
                          expandedRow === item.log_id && 'bg-muted/30'
                        )}
                      >
                        <td className='px-4 py-3 text-xs text-muted-foreground whitespace-nowrap'>
                          {formatTime(item.event_time)}
                        </td>
                        <td className='px-4 py-3 text-sm'>
                          <div className='flex items-center gap-1.5'>
                            <User className='h-3 w-3 text-muted-foreground' />
                            <span className='text-xs'>{item.user_name || '—'}</span>
                          </div>
                        </td>
                        <td className='px-4 py-3'>
                          <Badge
                            variant='secondary'
                            className={cn(
                              'text-xs font-medium',
                              getEventBadgeClassName(item.event_type)
                            )}
                          >
                            <EventIcon className='h-3 w-3' />
                            {item.event_type}
                          </Badge>
                        </td>
                        <td className='px-4 py-3 text-sm'>
                          <div className='space-y-1'>
                            <p className='text-xs font-medium'>{item.action}</p>
                            <p className='text-xs text-muted-foreground'>
                              {item.object_name
                                ? `${item.object_type} ${truncateText(item.object_name, 32)}`
                                : item.object_type || '—'}
                            </p>
                          </div>
                        </td>
                        <td className='px-4 py-3'>
                          <Badge
                            variant='secondary'
                            className={cn(
                              'text-xs font-medium',
                              getStatusBadgeClassName(item.status)
                            )}
                          >
                            {item.status}
                          </Badge>
                        </td>
                        <td className='px-4 py-3 text-right text-xs font-medium'>
                          {formatDuration(item.duration_ms)}
                        </td>
                        <td className='px-4 py-3 text-right text-xs text-muted-foreground'>
                          {(item.rows_affected ?? 0).toLocaleString()}
                        </td>
                      </tr>
                      {expandedRow === item.log_id ? (
                        <tr className='border-b border-border bg-muted/20'>
                          <td colSpan={7} className='px-4 py-4'>
                            <div className='space-y-3'>
                              {item.sql_text ? (
                                <div>
                                  <p className='mb-1.5 text-xs font-medium text-muted-foreground'>
                                    SQL Text
                                  </p>
                                  <pre className='max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs font-mono'>
                                    {item.sql_text}
                                  </pre>
                                </div>
                              ) : null}

                              {item.error_message ? (
                                <div>
                                  <p className='mb-1.5 flex items-center gap-1.5 text-xs font-medium text-destructive'>
                                    <AlertCircle className='h-3 w-3' />
                                    Error Message
                                  </p>
                                  <pre className='rounded-md bg-destructive/10 p-3 text-xs font-mono text-destructive'>
                                    {item.error_message}
                                  </pre>
                                </div>
                              ) : null}

                              <div className='flex flex-wrap gap-4 text-xs text-muted-foreground'>
                                <div>
                                  <span className='font-medium'>Log ID:</span>{' '}
                                  {item.log_id}
                                </div>
                                <div>
                                  <span className='font-medium'>Session:</span>{' '}
                                  {item.session_id || '—'}
                                </div>
                                <div>
                                  <span className='font-medium'>IP Address:</span>{' '}
                                  {item.ip_address || '—'}
                                </div>
                                <div>
                                  <span className='font-medium'>Database:</span>{' '}
                                  {item.database_name || '—'}
                                </div>
                                <div>
                                  <span className='font-medium'>Schema:</span>{' '}
                                  {item.schema_name || '—'}
                                </div>
                              </div>

                              {item.database_name || item.schema_name ? (
                                <div className='flex items-center gap-1.5 text-xs text-muted-foreground'>
                                  <Database className='h-3 w-3' />
                                  <span>
                                    {item.database_name || '—'}
                                    {item.schema_name
                                      ? `.${item.schema_name}`
                                      : ''}
                                  </span>
                                </div>
                              ) : null}
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className='flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between'>
        <div className='flex items-center gap-2 text-sm'>
          <span className='text-muted-foreground'>Rows per page:</span>
          <Select value={String(pageSize)} onValueChange={handlePageSizeChange}>
            <SelectTrigger className='w-[70px]'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='5'>5</SelectItem>
              <SelectItem value='10'>10</SelectItem>
              <SelectItem value='25'>25</SelectItem>
              <SelectItem value='50'>50</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className='flex flex-col gap-2 sm:flex-row sm:items-center'>
          <span className='text-sm text-muted-foreground'>
            Showing {pageStart}-{pageEnd} of {total}
          </span>
          <span className='text-sm text-muted-foreground'>
            Page {page} of {totalPages}
          </span>
          <div className='flex items-center gap-1'>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(1)}
              disabled={page === 1}
              className='h-8 w-8'
            >
              <ChevronsLeft className='h-4 w-4' />
            </Button>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(page - 1)}
              disabled={page === 1}
              className='h-8 w-8'
            >
              <ChevronLeft className='h-4 w-4' />
            </Button>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(page + 1)}
              disabled={page >= totalPages}
              className='h-8 w-8'
            >
              <ChevronRight className='h-4 w-4' />
            </Button>
            <Button
              variant='outline'
              size='icon'
              onClick={() => handlePageChange(totalPages)}
              disabled={page >= totalPages}
              className='h-8 w-8'
            >
              <ChevronsRight className='h-4 w-4' />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
