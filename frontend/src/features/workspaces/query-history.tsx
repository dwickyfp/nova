import { Clock, Copy, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { HistoryItem } from './types'

export function QueryHistory({
  items,
  loading,
  onLoadSql,
  onReRun,
}: {
  items: HistoryItem[]
  loading: boolean
  onLoadSql: (sql: string) => void
  onReRun: (sql: string) => void
}) {
  if (loading) {
    return (
      <div className='flex items-center justify-center p-6 text-sm text-muted-foreground'>
        Loading history...
      </div>
    )
  }

  if (!items.length) {
    return (
      <div className='flex flex-col items-center justify-center gap-2 p-6 text-sm text-muted-foreground'>
        <Clock className='size-8 opacity-40' />
        <span>No query history yet.</span>
      </div>
    )
  }

  function formatTime(iso: string) {
    if (!iso) return ''
    const d = new Date(iso)
    const now = new Date()
    const isToday =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate()
    const time = d.toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
    })
    if (isToday) return time
    const date = d.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
    })
    return `${date} ${time}`
  }

  return (
    <div className='flex flex-col'>
      {items.map((item) => (
        <div
          key={item.query_id}
          className='group flex items-start gap-3 border-b px-4 py-2.5 last:border-b-0 hover:bg-muted/50'
        >
          <div className='flex flex-1 flex-col gap-1 overflow-hidden'>
            <div className='flex items-center gap-2'>
              <span
                className={cn(
                  'inline-flex h-4 items-center rounded px-1 text-[10px] font-medium',
                  item.status === 'SUCCESS'
                    ? 'bg-success/10 text-success-strong'
                    : 'bg-destructive/10 text-destructive'
                )}
              >
                {item.status === 'SUCCESS' ? 'OK' : 'ERR'}
              </span>
              <span className='text-xs text-muted-foreground'>
                {formatTime(item.event_time)}
              </span>
              {item.duration_ms != null && (
                <span className='text-xs text-muted-foreground'>
                  {item.duration_ms}ms
                </span>
              )}
              {item.rows_affected != null && (
                <span className='text-xs text-muted-foreground'>
                  {item.rows_affected} rows
                </span>
              )}
              {item.database_name && (
                <span className='text-xs text-muted-foreground'>
                  {item.database_name}
                  {item.schema_name ? `.${item.schema_name}` : ''}
                </span>
              )}
            </div>
            <button
              type='button'
              className='w-full cursor-pointer text-left'
              onClick={() => onLoadSql(item.sql_text)}
              title={item.sql_text}
            >
              <code className='block truncate font-mono text-xs text-foreground/80'>
                {item.sql_text}
              </code>
            </button>
          </div>
          <div className='flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100'>
            <Button
              type='button'
              size='icon'
              variant='ghost'
              className='size-6'
              title='Copy SQL'
              aria-label='Copy SQL'
              onClick={() => navigator.clipboard.writeText(item.sql_text)}
            >
              <Copy className='size-3' />
            </Button>
            <Button
              type='button'
              size='icon'
              variant='ghost'
              className='size-6'
              title='Re-run query'
              aria-label='Re-run query'
              onClick={() => onReRun(item.sql_text)}
            >
              <RotateCcw className='size-3' />
            </Button>
          </div>
        </div>
      ))}
    </div>
  )
}
