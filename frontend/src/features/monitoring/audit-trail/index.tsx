import { useEffect, useMemo, useState } from 'react'
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
import { formatDateTime, formatDuration, formatNumber, truncate } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines, RefreshBanner } from '@/components/ui/loading-overlay'
import { PageHeader } from '@/components/ui/page-header'
import { StatusBadge } from '@/components/ui/status-badge'
import { statusLabel, statusTone } from '@/components/ui/status-tone'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
} from '@/components/ui/table'
import {
  SimpleTablePagination,
  SimpleTableToolbar,
} from '@/components/data-table/simple-table-controls'
import {
  DetailBlock,
  DetailFields,
  ExpandableRow,
  MonitoringTableHeader,
  MonitoringTableViewport,
  TableMessageRow,
} from '../components/expandable-row'

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
/** Seven data columns plus the details toggle. */
const COLUMN_COUNT = 8

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
  const hasFilters = Boolean(searchQuery || eventType || status || userName)
  // Search runs over the page already loaded, so while it is active the
  // label counts matches on this page instead of the server total.
  const resultLabel = searchQuery.trim()
    ? `${filteredItems.length} of ${queryItems.length} on this page`
    : `${total} ${total === 1 ? 'event' : 'events'}`

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
        resultLabel={resultLabel}
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

      <MonitoringTableViewport>
        {auditQuery.isFetching && !auditQuery.isLoading ? (
          <RefreshBanner label='Loading audit events...' />
        ) : null}

        <Table>
          <MonitoringTableHeader>
            <TableHead>Time</TableHead>
            <TableHead>User</TableHead>
            <TableHead>Event</TableHead>
            <TableHead>Action</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className='text-right'>Duration</TableHead>
            <TableHead className='text-right'>Rows</TableHead>
          </MonitoringTableHeader>
          <TableBody>
            {auditQuery.isLoading ? (
              <TableMessageRow colSpan={COLUMN_COUNT}>
                <LoadingLines rows={5} />
              </TableMessageRow>
            ) : auditQuery.isError ? (
              <TableMessageRow colSpan={COLUMN_COUNT}>
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
              </TableMessageRow>
            ) : filteredItems.length === 0 ? (
              <TableMessageRow colSpan={COLUMN_COUNT}>
                <EmptyState
                  icon={SearchX}
                  title={
                    hasFilters
                      ? 'No events match these filters'
                      : 'No audit events recorded yet'
                  }
                  description={
                    hasFilters
                      ? 'Clear the event type, status, or user filter to widen the search.'
                      : 'Events appear here as soon as someone queries, logs in, or changes a workspace.'
                  }
                  action={
                    hasFilters ? (
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
              </TableMessageRow>
            ) : (
              filteredItems.map((item) => {
                const EventIcon = EVENT_ICON[item.event_type] ?? Activity

                return (
                  <ExpandableRow
                    key={item.log_id}
                    expanded={expandedRow === item.log_id}
                    onToggle={() =>
                      setExpandedRow(
                        expandedRow === item.log_id ? null : item.log_id
                      )
                    }
                    label={`${item.event_type} event ${item.log_id}`}
                    colSpan={COLUMN_COUNT}
                    detail={
                      <>
                        {item.sql_text ? (
                          <DetailBlock label='SQL Text'>
                            {item.sql_text}
                          </DetailBlock>
                        ) : null}
                        {item.error_message ? (
                          <DetailBlock label='Error Message' tone='error'>
                            {item.error_message}
                          </DetailBlock>
                        ) : null}
                        <DetailFields
                          fields={[
                            { label: 'Log ID', value: item.log_id },
                            { label: 'Session', value: item.session_id },
                            { label: 'IP Address', value: item.ip_address },
                            { label: 'Database', value: item.database_name },
                            { label: 'Schema', value: item.schema_name },
                          ]}
                        />
                        {item.database_name || item.schema_name ? (
                          <div className='flex items-center gap-1.5 text-xs text-muted-foreground'>
                            <Database className='size-3' />
                            <span>
                              {item.database_name || '—'}
                              {item.schema_name ? `.${item.schema_name}` : ''}
                            </span>
                          </div>
                        ) : null}
                      </>
                    }
                  >
                    <TableCell className='px-4 py-3 text-xs text-muted-foreground'>
                      {formatDateTime(item.event_time, { seconds: true })}
                    </TableCell>
                    <TableCell className='px-4 py-3'>
                      <div className='flex items-center gap-1.5'>
                        <User className='size-3 text-muted-foreground' />
                        <span className='text-xs'>{item.user_name || '—'}</span>
                      </div>
                    </TableCell>
                    <TableCell className='px-4 py-3'>
                      <StatusBadge tone={eventTone(item.event_type)}>
                        <EventIcon className='size-3' />
                        {item.event_type}
                      </StatusBadge>
                    </TableCell>
                    <TableCell className='px-4 py-3'>
                      <div className='space-y-1'>
                        <p className='text-xs font-medium'>{item.action}</p>
                        <p className='text-xs text-muted-foreground'>
                          {item.object_name
                            ? `${item.object_type} ${truncate(item.object_name, 32)}`
                            : item.object_type || '—'}
                        </p>
                      </div>
                    </TableCell>
                    <TableCell className='px-4 py-3'>
                      <StatusBadge tone={statusTone(item.status)}>
                        {statusLabel(item.status)}
                      </StatusBadge>
                    </TableCell>
                    <TableCell className='px-4 py-3 text-right text-xs font-medium'>
                      {formatDuration(item.duration_ms)}
                    </TableCell>
                    <TableCell className='px-4 py-3 text-right text-xs text-muted-foreground'>
                      {formatNumber(item.rows_affected ?? 0)}
                    </TableCell>
                  </ExpandableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </MonitoringTableViewport>

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
