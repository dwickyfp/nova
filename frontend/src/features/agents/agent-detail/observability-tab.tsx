import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  type PaginationState,
  type SortingState,
  useReactTable,
} from '@tanstack/react-table'
import { Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { DataTablePagination } from '@/components/data-table'
import { observabilityApi, type SessionRow } from '@/features/agents/api'
import { ThreadTraceView } from './thread-trace-view'

/**
 * Agent Observability: one row per conversation. Opening a row shows its trace.
 *
 * The table shows only what the reader decides on: what the conversation was
 * about, how much work it holds, and when it last moved. The author is folded
 * into the row's detail line rather than a column, because every row here is the
 * caller's own.
 */
export function AgentObservabilityTab({ agentId }: { agentId: string }) {
  const [openThread, setOpenThread] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [sorting, setSorting] = useState<SortingState>([{ id: 'updated', desc: true }])
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  })

  const sessionsQuery = useQuery({
    queryKey: ['agents', 'observability', 'sessions', agentId],
    queryFn: () => observabilityApi.sessions(agentId, 200),
  })

  const rows = useMemo(() => {
    const all = sessionsQuery.data?.sessions ?? []
    const term = search.trim().toLowerCase()
    if (!term) return all
    return all.filter(
      (s) =>
        s.first_input.toLowerCase().includes(term) ||
        s.user_name.toLowerCase().includes(term) ||
        s.thread_id.toLowerCase().includes(term)
    )
  }, [sessionsQuery.data, search])

  const columns = useMemo<ColumnDef<SessionRow>[]>(
    () => [
      {
        id: 'input',
        accessorFn: (row) => row.first_input,
        header: 'Conversation',
        cell: ({ row }) => (
          <div className='min-w-0'>
            <div className='line-clamp-1 text-sm'>
              {row.original.first_input || 'No input recorded'}
            </div>
            <div className='font-mono text-xs text-muted-foreground'>
              {row.original.thread_id.slice(0, 8)}
            </div>
          </div>
        ),
        meta: { className: 'min-w-0' },
      },
      {
        id: 'turns',
        accessorFn: (row) => row.message_count,
        header: 'Turns',
        cell: ({ row }) => (
          <div className='text-right tabular-nums'>{row.original.message_count}</div>
        ),
      },
      {
        id: 'tokens',
        accessorFn: (row) => row.total_tokens,
        header: 'Tokens',
        cell: ({ row }) => (
          <div className='text-right tabular-nums'>
            {row.original.total_tokens > 0
              ? row.original.total_tokens.toLocaleString()
              : 'Not recorded'}
          </div>
        ),
      },
      {
        id: 'updated',
        accessorFn: (row) => row.updated_at,
        header: 'Last updated',
        cell: ({ row }) => (
          <div className='whitespace-nowrap text-muted-foreground'>
            {new Date(row.original.updated_at).toLocaleString()}
          </div>
        ),
      },
    ],
    []
  )

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting, pagination },
    onSortingChange: setSorting,
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  })

  // A filter that shrinks the list can leave the reader on a page that no longer
  // exists. Pull the page back into range when that happens.
  useEffect(() => {
    const pageCount = table.getPageCount()
    if (pageCount > 0 && pagination.pageIndex >= pageCount) {
      setPagination((prev) => ({ ...prev, pageIndex: 0 }))
    }
  }, [table, pagination.pageIndex])

  if (openThread) {
    return (
      <ThreadTraceView
        agentId={agentId}
        threadId={openThread}
        onClose={() => setOpenThread(null)}
      />
    )
  }

  const total = rows.length
  const pageLabel = sessionsQuery.isLoading
    ? 'Loading'
    : `${total} conversation${total === 1 ? '' : 's'}`

  return (
    <div className='flex flex-1 flex-col gap-4'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <div>
          <h2 className='text-lg font-medium'>Conversations</h2>
          <p className='mt-0.5 text-sm text-muted-foreground'>{pageLabel}</p>
        </div>
        <div className='relative w-64'>
          <Search className='absolute left-2.5 top-2.5 size-4 text-muted-foreground' />
          <Input
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPagination((prev) => ({ ...prev, pageIndex: 0 }))
            }}
            placeholder='Search by input, user, or id'
            className='pl-8'
            aria-label='Search conversations'
          />
        </div>
      </div>

      {sessionsQuery.isLoading ? (
        <Skeleton className='h-64 w-full' />
      ) : sessionsQuery.isError ? (
        <div className='rounded-md border border-destructive/40 bg-destructive/5 p-6 text-center'>
          <p className='text-sm font-medium'>Could not load conversations</p>
          <p className='mt-1 text-sm text-muted-foreground'>
            The request failed. Retry, or check the backend is running.
          </p>
          <Button
            variant='outline'
            size='sm'
            className='mt-3'
            onClick={() => void sessionsQuery.refetch()}
          >
            Retry
          </Button>
        </div>
      ) : total === 0 ? (
        <div className='rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground'>
          {search
            ? 'No conversation matches that search.'
            : 'No conversations yet. Chat with this agent in Nova Studio.'}
        </div>
      ) : (
        <>
          <div className='overflow-hidden rounded-md border'>
            <Table>
              <TableHeader>
                {table.getHeaderGroups().map((headerGroup) => (
                  <TableRow key={headerGroup.id} className='group/row'>
                    {headerGroup.headers.map((header) => (
                      <TableHead
                        key={header.id}
                        colSpan={header.colSpan}
                        className={cn(
                          'bg-background group-hover/row:bg-muted',
                          header.column.columnDef.meta?.className
                        )}
                      >
                        {header.isPlaceholder
                          ? null
                          : flexRender(
                              header.column.columnDef.header,
                              header.getContext()
                            )}
                      </TableHead>
                    ))}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {table.getRowModel().rows.map((row) => (
                  <TableRow
                    key={row.id}
                    className='group/row cursor-pointer'
                    onClick={() => setOpenThread(row.original.thread_id)}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell
                        key={cell.id}
                        className={cn(
                          'bg-background group-hover/row:bg-muted',
                          cell.column.columnDef.meta?.className
                        )}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <DataTablePagination table={table} className='mt-auto' />
        </>
      )}
    </div>
  )
}
