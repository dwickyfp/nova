import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Activity,
  AlertTriangle,
  Database,
  User,
  XCircle,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { formatSeconds, truncate } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines, RefreshBanner } from '@/components/ui/loading-overlay'
import { MonitoringHeader } from '../components/monitoring-header'
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

type ActiveQuery = {
  id: number
  user: string
  host: string
  db: string | null
  command: string
  time: number
  state: string
  info: string | null
  query_id: string | null
}

/** Seven data columns plus the details toggle. */
const COLUMN_COUNT = 8

/** Command is a classification, not a health state, so it stays informational. */
function commandTone(command: string) {
  const normalized = command.toUpperCase()
  if (normalized === 'QUERY') return 'info' as const
  if (normalized === 'SLEEP') return 'neutral' as const
  return 'primary' as const
}

/**
 * `embedded` drops the page header when the view sits inside the Queries
 * tabs, which carry their own title.
 */
export function MonitoringActiveQueries({ embedded = false }: { embedded?: boolean } = {}) {
  const queryClient = useQueryClient()
  const [userFilter, setUserFilter] = useState('')
  const [databaseFilter, setDatabaseFilter] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [killTarget, setKillTarget] = useState<ActiveQuery | null>(null)
  const [expandedRow, setExpandedRow] = useState<number | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const { data, isLoading, isFetching } = useQuery<ActiveQuery[]>({
    queryKey: ['monitoring', 'active-queries'],
    queryFn: () => api.get<ActiveQuery[]>('/monitoring/queries/active'),
    refetchInterval: 5000,
  })

  const killMutation = useMutation({
    mutationFn: (query: ActiveQuery) =>
      api.post('/monitoring/queries/kill', { connection_id: query.id }),
    onSuccess: () => {
      toast.success('Query killed successfully')
      void queryClient.invalidateQueries({
        queryKey: ['monitoring', 'active-queries'],
      })
      setKillTarget(null)
    },
    onError: (error: Error) => {
      toast.error(`Failed to kill query: ${error.message}`)
      setKillTarget(null)
    },
  })

  const queries = data ?? []

  const userOptions = useMemo(() => {
    return [
      ...new Set(queries.map((query) => query.user).filter(Boolean)),
    ] as string[]
  }, [queries])

  const databaseOptions = useMemo(() => {
    return [
      ...new Set(queries.map((query) => query.db).filter(Boolean)),
    ] as string[]
  }, [queries])

  const filteredQueries = useMemo(() => {
    const normalizedSearchQuery = searchQuery.trim().toLowerCase()

    return queries.filter((query) => {
      if (userFilter && query.user !== userFilter) return false
      if (databaseFilter && query.db !== databaseFilter) return false
      if (!normalizedSearchQuery) return true

      return [
        query.info ?? '',
        query.state,
        query.command,
        query.host,
        query.query_id ?? '',
      ].some((value) => value.toLowerCase().includes(normalizedSearchQuery))
    })
  }, [databaseFilter, queries, searchQuery, userFilter])

  const total = filteredQueries.length
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const safePage = Math.min(page, totalPages)
  const pagedQueries = filteredQueries.slice(
    (safePage - 1) * pageSize,
    safePage * pageSize
  )

  const handlePageChange = (newPage: number) => {
    setPage(Math.max(1, Math.min(newPage, totalPages)))
    setExpandedRow(null)
  }

  const handleFilterChange = (
    setter: (value: string) => void,
    value: string
  ) => {
    setter(value)
    setPage(1)
    setExpandedRow(null)
  }

  return (
    <div className='space-y-6'>
      <MonitoringHeader
        embedded={embedded}
        title='Active Queries'
        description='Queries running right now, with their execution details.'
      />

      <SimpleTableToolbar
        search={searchQuery}
        onSearchChange={(value) => {
          setSearchQuery(value)
          setPage(1)
          setExpandedRow(null)
        }}
        searchPlaceholder='Search query, host, state...'
        resultLabel={`${total} ${total === 1 ? 'active query' : 'active queries'}`}
        filters={[
          {
            label: 'User',
            value: userFilter,
            options: userOptions,
            onChange: (value) => handleFilterChange(setUserFilter, value),
            icon: <User size={14} />,
          },
          {
            label: 'Database',
            value: databaseFilter,
            options: databaseOptions,
            onChange: (value) => handleFilterChange(setDatabaseFilter, value),
            icon: <Database size={14} />,
          },
        ]}
      />

      <MonitoringTableViewport>
        {isFetching && !isLoading ? (
          <RefreshBanner label='Refreshing active queries...' />
        ) : null}

        <Table>
          <MonitoringTableHeader>
            <TableHead>User</TableHead>
            <TableHead>Database</TableHead>
            <TableHead>Command</TableHead>
            <TableHead>Query</TableHead>
            <TableHead>State</TableHead>
            <TableHead className='text-right'>Time</TableHead>
            <TableHead className='text-right'>Actions</TableHead>
          </MonitoringTableHeader>
          <TableBody>
            {isLoading ? (
              <TableMessageRow colSpan={COLUMN_COUNT}>
                <LoadingLines rows={5} />
              </TableMessageRow>
            ) : pagedQueries.length === 0 ? (
              <TableMessageRow colSpan={COLUMN_COUNT}>
                <EmptyState
                  icon={Activity}
                  title={
                    queries.length > 0
                      ? 'No active queries match these filters'
                      : 'Nothing is running right now'
                  }
                  description={
                    queries.length > 0
                      ? 'Clear the user or database filter to see the full list.'
                      : 'This view shows queries in flight. It refreshes every 5 seconds.'
                  }
                />
              </TableMessageRow>
            ) : (
              pagedQueries.map((query) => (
                <ExpandableRow
                  key={query.id}
                  expanded={expandedRow === query.id}
                  onToggle={() =>
                    setExpandedRow(expandedRow === query.id ? null : query.id)
                  }
                  label={`connection ${query.id}`}
                  colSpan={COLUMN_COUNT}
                  detail={
                    <>
                      <DetailBlock label='Full Query'>
                        {query.info ?? '—'}
                      </DetailBlock>
                      <DetailFields
                        fields={[
                          { label: 'Connection ID', value: query.id },
                          { label: 'Host', value: query.host },
                          { label: 'Query ID', value: query.query_id },
                          { label: 'Command', value: query.command },
                        ]}
                      />
                      {query.state ? (
                        <DetailBlock label='Current State'>
                          {query.state}
                        </DetailBlock>
                      ) : null}
                    </>
                  }
                >
                  <TableCell className='px-4 py-3'>
                    <div className='flex items-center gap-1.5'>
                      <User className='size-3 text-muted-foreground' />
                      <span className='text-xs'>{query.user}</span>
                    </div>
                  </TableCell>
                  <TableCell className='px-4 py-3'>
                    <div className='flex items-center gap-1.5'>
                      <Database className='size-3 text-muted-foreground' />
                      <span className='text-xs'>{query.db ?? '—'}</span>
                    </div>
                  </TableCell>
                  <TableCell className='px-4 py-3'>
                    <StatusBadge tone={commandTone(query.command)}>
                      {query.command}
                    </StatusBadge>
                  </TableCell>
                  <TableCell className='px-4 py-3'>
                    <code className='rounded bg-muted px-1.5 py-0.5 font-mono text-xs'>
                      {truncate(query.info ?? '—')}
                    </code>
                  </TableCell>
                  <TableCell className='px-4 py-3'>
                    <StatusBadge tone={statusTone(query.state)} dot>
                      {statusLabel(query.state)}
                    </StatusBadge>
                  </TableCell>
                  <TableCell className='px-4 py-3 text-right text-xs font-medium'>
                    {formatSeconds(query.time ?? 0)}
                  </TableCell>
                  <TableCell className='px-4 py-3 text-right'>
                    <Button
                      variant='ghost'
                      size='sm'
                      onClick={(event) => {
                        event.stopPropagation()
                        setKillTarget(query)
                      }}
                      className='text-destructive hover:text-destructive'
                    >
                      <XCircle className='size-4' />
                      Kill
                    </Button>
                  </TableCell>
                </ExpandableRow>
              ))
            )}
          </TableBody>
        </Table>
      </MonitoringTableViewport>

      <SimpleTablePagination
        page={safePage}
        pageSize={pageSize}
        total={total}
        onPageChange={handlePageChange}
        onPageSizeChange={(value) => {
          setPageSize(value)
          setPage(1)
          setExpandedRow(null)
        }}
      />

      <Dialog open={!!killTarget} onOpenChange={() => setKillTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className='flex items-center gap-2'>
              <AlertTriangle className='h-5 w-5 text-destructive' />
              Kill Query
            </DialogTitle>
            <DialogDescription>
              Are you sure you want to kill this query? This action cannot be
              undone.
            </DialogDescription>
          </DialogHeader>
          {killTarget ? (
            <div className='rounded-md bg-muted p-3 text-sm'>
              <p>
                <span className='font-medium'>Connection ID:</span>{' '}
                {killTarget.id}
              </p>
              <p>
                <span className='font-medium'>User:</span> {killTarget.user}
              </p>
              <p>
                <span className='font-medium'>Query:</span>{' '}
                <code className='text-xs'>
                  {truncate(killTarget.info ?? 'N/A', 100)}
                </code>
              </p>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant='outline' onClick={() => setKillTarget(null)}>
              Cancel
            </Button>
            <Button
              variant='destructive'
              onClick={() =>
                killTarget ? killMutation.mutate(killTarget) : null
              }
              disabled={killMutation.isPending}
            >
              {killMutation.isPending ? 'Killing...' : 'Kill Query'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
