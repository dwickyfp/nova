import { Fragment, useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Code,
  Database,
  FolderOpen,
  LogIn,
  SearchX,
  User,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines, RefreshBanner } from '@/components/ui/loading-overlay'
import { PageHeader } from '@/components/ui/page-header'
import { StatusBadge } from '@/components/ui/status-badge'
import {
  SimpleTablePagination,
  SimpleTableToolbar,
  SimpleTableViewport,
} from '@/components/data-table/simple-table-controls'
import { statusTone } from '../components/status-tone'

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

/** Event type is a category, not a health state, so it stays informational. */
function eventTone(eventType: string) {
  const normalized = eventType.toLowerCase()
  if (normalized === 'query') return 'info' as const
  if (normalized === 'workspace') return 'primary' as const
  return 'neutral' as const
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

  const userOptions = useMemo(() => {
    return [
      ...new Set(queryItems.map((item) => item.user_name).filter(Boolean)),
    ] as string[]
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

  return (
    <div className='space-y-6'>
      <PageHeader
        title='Audit Trail'
        description='Track user actions, schema changes, and access events.'
      />

      <SimpleTableToolbar
        search={searchQuery}
        onSearchChange={(value) => {
          setSearchQuery(value)
          setExpandedRow(null)
        }}
        searchPlaceholder='Search action, object, SQL...'
        resultLabel={`${total} ${total === 1 ? 'event' : 'events'}`}
        filters={[
          {
            label: 'Event Type',
            value: eventType,
            options: EVENT_TYPE_OPTIONS,
            onChange: (value) => handleFilterChange(setEventType, value),
            icon: <Activity size={14} />,
          },
          {
            label: 'Status',
            value: status,
            options: STATUS_OPTIONS,
            onChange: (value) => handleFilterChange(setStatus, value),
            icon: <CheckCircle2 size={14} />,
          },
          {
            label: 'User',
            value: userName,
            options: userOptions,
            onChange: (value) => handleFilterChange(setUserName, value),
            icon: <User size={14} />,
          },
        ]}
      />

      <SimpleTableViewport>
        {auditQuery.isFetching && !auditQuery.isLoading ? (
          <RefreshBanner label='Loading audit events...' />
        ) : null}

        <table className='w-full'>
          <thead>
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
                <td colSpan={7} className='px-4 py-6'>
                  <LoadingLines rows={5} />
                </td>
              </tr>
            ) : auditQuery.isError ? (
              <tr>
                <td colSpan={7} className='px-4 py-6'>
                  <EmptyState
                    variant='error'
                    icon={AlertCircle}
                    title='Could not load the audit trail'
                    description='The monitoring API did not respond. Check the connection and retry.'
                    action={
                      <Button
                        variant='outline'
                        size='sm'
                        onClick={() => void auditQuery.refetch()}
                      >
                        Retry
                      </Button>
                    }
                  />
                </td>
              </tr>
            ) : filteredItems.length === 0 ? (
              <tr>
                <td colSpan={7} className='px-4 py-6'>
                  <EmptyState
                    icon={SearchX}
                    title={
                      searchQuery || eventType || status || userName
                        ? 'No events match these filters'
                        : 'No audit events recorded yet'
                    }
                    description={
                      searchQuery || eventType || status || userName
                        ? 'Clear the event type, status, or user filter to widen the search.'
                        : 'Events appear here as soon as someone queries, logs in, or changes a workspace.'
                    }
                    action={
                      searchQuery || eventType || status || userName ? (
                        <Button
                          variant='outline'
                          size='sm'
                          onClick={() => {
                            setSearchQuery('')
                            setEventType('')
                            setStatus('')
                            setUserName('')
                            setPage(1)
                          }}
                        >
                          Clear filters
                        </Button>
                      ) : undefined
                    }
                  />
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
                          <span className='text-xs'>
                            {item.user_name || '—'}
                          </span>
                        </div>
                      </td>
                      <td className='px-4 py-3'>
                        <StatusBadge tone={eventTone(item.event_type)}>
                          <EventIcon className='h-3 w-3' />
                          {item.event_type}
                        </StatusBadge>
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
                        <StatusBadge tone={statusTone(item.status)}>
                          {item.status}
                        </StatusBadge>
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
      </SimpleTableViewport>

      <SimpleTablePagination
        page={page}
        pageSize={pageSize}
        total={total}
        pageSizes={[5, 10, 25, 50]}
        onPageChange={handlePageChange}
        onPageSizeChange={(value) => {
          setPageSize(value)
          setPage(1)
          setExpandedRow(null)
        }}
      />
    </div>
  )
}
