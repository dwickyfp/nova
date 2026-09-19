import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertCircle, RefreshCw, Table2 } from 'lucide-react'
import { api } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines } from '@/components/ui/loading-overlay'
import type { QueryResponse } from '@/features/workspaces/types'

const PREVIEW_LIMIT = 10

function quoteIdentifier(identifier: string) {
  return `\`${identifier.replace(/`/g, '``')}\``
}

export function PreviewDataTab({
  database,
  table,
}: {
  database: string
  table: string
}) {
  const [refetchKey, setRefetchKey] = useState(0)

  const previewQuery = useQuery<QueryResponse[]>({
    queryKey: ['explorer-table-preview', database, table, refetchKey],
    queryFn: () =>
      api.post<QueryResponse[]>('/query/execute', {
        sql: `SELECT * FROM ${quoteIdentifier(database)}.${quoteIdentifier(table)} LIMIT ${PREVIEW_LIMIT}`,
        database,
        max_rows: PREVIEW_LIMIT,
      }),
  })

  useEffect(() => {
    if (previewQuery.error) {
      toast.error('Failed to load preview data', {
        description: previewQuery.error.message,
      })
    }
  }, [previewQuery.error])

  const result = previewQuery.data?.[0] ?? null
  const errorMessage = previewQuery.error
    ? previewQuery.error.message
    : result && !result.success
      ? result.error || 'The engine rejected the preview query.'
      : null

  const rows = useMemo(() => result?.rows ?? [], [result])

  if (previewQuery.isLoading) {
    return <LoadingLines rows={6} />
  }

  if (errorMessage) {
    return (
      <EmptyState
        variant='error'
        icon={AlertCircle}
        title='Could not load preview data'
        description={errorMessage}
        action={
          <Button
            variant='outline'
            size='sm'
            onClick={() => setRefetchKey((key) => key + 1)}
          >
            Retry
          </Button>
        }
      />
    )
  }

  if (!result || rows.length === 0) {
    return (
      <EmptyState
        icon={Table2}
        title='No rows to preview'
        description={`The table returned 0 rows in the first ${PREVIEW_LIMIT}.`}
      />
    )
  }

  return (
    <div className='space-y-3'>
      <div className='flex items-center justify-between gap-2 text-sm text-muted-foreground'>
        <span>
          Previewing the first {rows.length} of up to {PREVIEW_LIMIT} rows
          {result.elapsed_ms ? ` · ${result.elapsed_ms} ms` : ''}
        </span>
        <Button
          variant='ghost'
          size='sm'
          className='h-8 gap-1.5 px-2 text-xs'
          onClick={() => setRefetchKey((key) => key + 1)}
          disabled={previewQuery.isFetching}
        >
          <RefreshCw
            className={`size-3.5 ${previewQuery.isFetching ? 'animate-spin' : ''}`}
          />
          Refresh
        </Button>
      </div>

      <div className='overflow-x-auto rounded-lg border border-border'>
        <table className='w-full text-sm'>
          <thead className='bg-muted'>
            <tr className='border-b border-border text-left text-xs font-medium text-muted-foreground'>
              {result.columns.map((column, index) => (
                <th key={`${column}-${index}`} className='px-4 py-3 whitespace-nowrap'>
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr
                key={rowIndex}
                className='border-b border-border transition-colors last:border-0 hover:bg-muted/50'
              >
                {row.map((cell, cellIndex) => (
                  <td
                    key={cellIndex}
                    className='px-4 py-2.5 font-mono text-xs whitespace-nowrap'
                  >
                    {cell === null || cell === undefined ? (
                      <span className='text-muted-foreground italic'>NULL</span>
                    ) : (
                      String(cell)
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
