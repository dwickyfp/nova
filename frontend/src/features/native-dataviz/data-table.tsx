import { useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table'
import { defaultRangeExtractor, useVirtualizer, type Range } from '@tanstack/react-virtual'
import { cn } from '@/lib/utils'
import type { DatasetField, DatasetPreview } from './contracts'

type Row = DatasetPreview['rows'][number]
type Value = Row[number]

type DataTableProps = {
  fields: DatasetField[]
  rows: DatasetPreview['rows']
  label: string
  /** Include dataset, revision, page and security epoch so focus cannot outlive its data. */
  contextKey: string
  className?: string
}

const ROW_HEIGHT = 36
const HEADER_HEIGHT = 36

function columnWidth(field: DatasetField): number {
  switch (field.logical_type) {
    case 'boolean': return 112
    case 'date': return 144
    case 'integer': return 224
    case 'decimal': return 416
    case 'timestamp': return 336
    default: return 224
  }
}

function displayValue(value: Value): string {
  if (value === null) return 'NULL'
  if (value.type === 'boolean') return value.value ? 'true' : 'false'
  if (value.type === 'string' && value.value === '') return 'Empty string'
  return String(value.value)
}

function includeActive(range: Range, active: number): number[] {
  const visible = defaultRangeExtractor(range)
  return visible.includes(active) ? visible : [...visible, active].sort((a, b) => a - b)
}

export function DataTable(props: DataTableProps) {
  return <DataGrid key={props.contextKey} {...props} />
}

function DataGrid({ fields, rows, label, className }: DataTableProps) {
  const viewport = useRef<HTMLDivElement>(null)
  const id = useId()
  const [active, setActive] = useState({ row: 0, column: 0 })
  const columns = useMemo<ColumnDef<Row>[]>(() => fields.map((field, index) => ({
    id: field.field_id,
    header: field.name,
    accessorFn: row => row[index],
    size: columnWidth(field),
  })), [fields])
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() })
  const model = table.getRowModel().rows
  const leafColumns = table.getVisibleLeafColumns()
  const virtualRows = useVirtualizer({
    count: model.length,
    getScrollElement: () => viewport.current,
    estimateSize: () => ROW_HEIGHT,
    paddingStart: HEADER_HEIGHT,
    scrollPaddingStart: HEADER_HEIGHT,
    overscan: 4,
    rangeExtractor: range => includeActive(range, active.row),
  })
  const virtualColumns = useVirtualizer({
    count: leafColumns.length,
    horizontal: true,
    getScrollElement: () => viewport.current,
    estimateSize: index => leafColumns[index].getSize(),
    overscan: 1,
    rangeExtractor: range => includeActive(range, active.column),
  })
  const cellId = (row: number, column: number) => `${id}-${row}-${column}`

  function select(row: number, column: number) {
    setActive({ row, column })
    virtualRows.scrollToIndex(row, { align: 'auto' })
    virtualColumns.scrollToIndex(column, { align: 'auto' })
    viewport.current?.focus({ preventScroll: true })
  }

  function navigate(event: KeyboardEvent<HTMLDivElement>) {
    if (!model.length || event.altKey || event.metaKey) return
    let { row, column } = active
    const page = Math.max(1, Math.floor(((viewport.current?.clientHeight ?? ROW_HEIGHT)
      - HEADER_HEIGHT) / ROW_HEIGHT))
    switch (event.key) {
      case 'ArrowDown': row++; break
      case 'ArrowUp': row--; break
      case 'ArrowRight': column++; break
      case 'ArrowLeft': column--; break
      case 'Home': column = 0; if (event.ctrlKey) row = 0; break
      case 'End': column = leafColumns.length - 1; if (event.ctrlKey) row = model.length - 1; break
      case 'PageDown': row += page; break
      case 'PageUp': row -= page; break
      default: return
    }
    event.preventDefault()
    select(Math.max(0, Math.min(model.length - 1, row)),
      Math.max(0, Math.min(leafColumns.length - 1, column)))
  }

  if (!rows.length) {
    return <div role='status' className='flex min-h-0 flex-1 items-center justify-center p-4 text-sm text-muted-foreground'>
      No rows returned.
    </div>
  }

  return <>
    <p id={`${id}-help`} className='sr-only'>
      Use arrow keys to move between cells. Home and End move across a row.
      Control Home and Control End move to the first and last cell on this page.
    </p>
    <div ref={viewport} role='grid' aria-label={label} aria-readonly='true'
      aria-rowcount={rows.length + 1} aria-colcount={fields.length}
      aria-describedby={`${id}-help`} aria-activedescendant={cellId(active.row, active.column)}
      tabIndex={0} onKeyDown={navigate}
      className={cn('group relative min-h-0 min-w-0 flex-1 overflow-auto border bg-card text-sm outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring', className)}>
      <div role='rowgroup' style={{ height: virtualRows.getTotalSize(), width: virtualColumns.getTotalSize() }}>
        <div role='row' aria-rowindex={1}
          className='sticky top-0 z-10 border-b bg-muted text-foreground'
          style={{ height: HEADER_HEIGHT, width: virtualColumns.getTotalSize() }}>
          {virtualColumns.getVirtualItems().map(column => <div
            key={leafColumns[column.index].id} role='columnheader' aria-colindex={column.index + 1}
            title={fields[column.index].name}
            className='absolute inset-y-0 flex items-center truncate border-e px-3 font-medium'
            style={{ left: column.start, width: column.size }}>
            <span className='truncate'>{fields[column.index].name}</span>
          </div>)}
        </div>
        {virtualRows.getVirtualItems().map(row => <div key={row.index} role='row'
          aria-rowindex={row.index + 2} className='absolute left-0 border-b'
          style={{ top: row.start, height: row.size, width: virtualColumns.getTotalSize() }}>
          {virtualColumns.getVirtualItems().map(column => {
            const field = fields[column.index]
            const value = model[row.index].getValue<Value>(field.field_id)
            const numeric = ['integer', 'decimal', 'float'].includes(field.logical_type)
            const empty = value === null || (value.type === 'string' && value.value === '')
            const selected = active.row === row.index && active.column === column.index
            return <div key={field.field_id} id={cellId(row.index, column.index)} role='gridcell'
              aria-colindex={column.index + 1} title={displayValue(value)}
              onClick={() => select(row.index, column.index)}
              className={cn('absolute inset-y-0 flex items-center border-e px-3',
                numeric && 'justify-end tabular-nums', empty && 'text-muted-foreground italic',
                selected && 'group-focus:bg-accent group-focus:ring-1 group-focus:ring-inset group-focus:ring-ring')}
              style={{ left: column.start, width: column.size }}>
              <span className='truncate'>{displayValue(value)}</span>
            </div>
          })}
        </div>)}
      </div>
    </div>
  </>
}
