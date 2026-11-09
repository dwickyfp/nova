import { useId } from 'react'
import { AlertCircle, ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { SimpleTableViewport } from '@/components/data-table/simple-table-controls'

/**
 * The monitoring pages list records that open into a detail panel. These
 * pieces keep that pattern identical on every page: the same table frame,
 * the same keyboard-reachable toggle, and the same "label: value" metadata.
 */

/**
 * `SimpleTableViewport` scrolls the table, so the inner table container must
 * not open a second scroll box; otherwise the sticky header would stick to it.
 */
export function MonitoringTableViewport({
  className,
  children,
}: {
  className?: string
  children: React.ReactNode
}) {
  return (
    <SimpleTableViewport
      className={cn(
        '[&>[data-slot=table-container]]:overflow-visible',
        className
      )}
    >
      {children}
    </SimpleTableViewport>
  )
}

/** Sticky column header row with the monitoring column style. */
export function MonitoringTableHeader({
  children,
  expandable = true,
}: {
  children: React.ReactNode
  /** Adds the leading column that holds each row's details toggle. */
  expandable?: boolean
}) {
  return (
    <TableHeader className='sticky top-0 z-20 bg-muted [&_th]:px-4 [&_th]:text-xs [&_th]:text-muted-foreground'>
      <TableRow className='hover:bg-transparent'>
        {expandable ? (
          <TableHead className='w-10'>
            <span className='sr-only'>Details</span>
          </TableHead>
        ) : null}
        {children}
      </TableRow>
    </TableHeader>
  )
}

/** A full-width row for loading, error and empty states. */
export function TableMessageRow({
  colSpan,
  children,
}: {
  colSpan: number
  children: React.ReactNode
}) {
  return (
    <TableRow className='hover:bg-transparent'>
      <TableCell colSpan={colSpan} className='px-4 py-6 whitespace-normal'>
        {children}
      </TableCell>
    </TableRow>
  )
}

type ExpandableRowProps = {
  expanded: boolean
  onToggle: () => void
  /** Names the record for the toggle's accessible name. */
  label: string
  /** Total columns, including the toggle column. */
  colSpan: number
  detail: React.ReactNode
  /** The data cells, after the toggle cell. */
  children: React.ReactNode
}

export function ExpandableRow({
  expanded,
  onToggle,
  label,
  colSpan,
  detail,
  children,
}: ExpandableRowProps) {
  const detailId = useId()

  return (
    <>
      <TableRow
        onClick={onToggle}
        className={cn('cursor-pointer', expanded && 'bg-muted/30')}
      >
        <TableCell className='w-10 py-3 ps-3 pe-0'>
          <Button
            type='button'
            variant='ghost'
            size='icon'
            className='size-7 text-muted-foreground'
            aria-expanded={expanded}
            aria-controls={expanded ? detailId : undefined}
            aria-label={`Details for ${label}`}
            onClick={(event) => {
              event.stopPropagation()
              onToggle()
            }}
          >
            {expanded ? (
              <ChevronDown className='size-4' />
            ) : (
              <ChevronRight className='size-4' />
            )}
          </Button>
        </TableCell>
        {children}
      </TableRow>
      {expanded ? (
        <TableRow id={detailId} className='bg-muted/20 hover:bg-muted/20'>
          <TableCell colSpan={colSpan} className='px-4 py-4 whitespace-normal'>
            <div className='space-y-3'>{detail}</div>
          </TableCell>
        </TableRow>
      ) : null}
    </>
  )
}

export type DetailField = {
  label: string
  value: React.ReactNode
  mono?: boolean
}

/** The "label: value" metadata line under an expanded row. */
export function DetailFields({ fields }: { fields: DetailField[] }) {
  return (
    <dl className='flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-muted-foreground'>
      {fields.map((field) => (
        <div key={field.label} className='flex gap-1'>
          <dt className='font-medium'>{field.label}:</dt>
          <dd className={cn('break-all', field.mono && 'font-mono')}>
            {field.value === null ||
            field.value === undefined ||
            field.value === ''
              ? '—'
              : field.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** A labelled block of code or message text inside an expanded row. */
export function DetailBlock({
  label,
  children,
  tone = 'default',
}: {
  label: string
  children: React.ReactNode
  tone?: 'default' | 'error'
}) {
  const isError = tone === 'error'

  return (
    <div>
      <p
        className={cn(
          'mb-1.5 flex items-center gap-1.5 text-xs font-medium',
          isError ? 'text-destructive' : 'text-muted-foreground'
        )}
      >
        {isError ? <AlertCircle className='size-3' aria-hidden='true' /> : null}
        {label}
      </p>
      <pre
        className={cn(
          'max-h-64 overflow-auto rounded-md p-3 font-mono text-xs whitespace-pre-wrap break-all',
          isError ? 'bg-destructive/10 text-destructive' : 'bg-muted'
        )}
      >
        {children}
      </pre>
    </div>
  )
}
