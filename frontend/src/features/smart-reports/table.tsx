import { useMemo } from 'react'
import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table'
import { compareDecimal } from './decimal'
import { Button } from '@/components/ui/button'
import type { Interaction, ViewState, Widget, WidgetResult } from './contracts'

export function ReportTable({ result, state, onEvent, widget }: { widget?: Widget; result: WidgetResult; state?: ViewState; onEvent?: (event: Interaction) => void }) {
  const columns = useMemo(() => result.columns.map((field, i) => ({ id: field, accessorFn: (row: typeof result.rows[number]) => row[i], header: result.column_labels?.[field] ?? field,
    cell: ({ getValue }: { getValue: () => unknown }) => String(getValue() ?? '—'),
  })), [result.columns, result.column_labels])
  const table = useReactTable({ data: result.rows, columns, getCoreRowModel: getCoreRowModel(), manualSorting: true, manualPagination: true })
  return <div className="min-w-0 space-y-2">
    <div className="overflow-x-auto rounded-md border" tabIndex={0} aria-label="Report data">
      <table className="w-full text-sm tabular-nums">
        <thead>{table.getHeaderGroups().map(group => <tr key={group.id} className="border-b bg-muted/40">{group.headers.map(header => <th key={header.id} scope="col" className="whitespace-nowrap p-2 text-start font-medium" aria-sort={state?.sort[result.widget_id]?.[0]?.field === header.id ? state.sort[result.widget_id][0].direction === 'asc' ? 'ascending' : 'descending' : undefined}>
          {onEvent && widget?.kind !== 'pivot' ? <button className="rounded-sm focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onEvent({ type: 'TABLE_SORT', target: result.widget_id, sort: [{ field: header.id, direction: state?.sort[result.widget_id]?.[0]?.direction === 'asc' ? 'desc' : 'asc' }] })}>{flexRender(header.column.columnDef.header, header.getContext())}</button> : flexRender(header.column.columnDef.header, header.getContext())}
        </th>)}</tr>)}</thead>
        <tbody>{table.getRowModel().rows.map(row => <tr key={row.id} className="border-b last:border-0" aria-label={result.row_kinds?.[row.index] === 'subtotal' ? 'Subtotal' : undefined}>{row.getVisibleCells().map(cell => <td key={cell.id} className={`whitespace-nowrap p-2 ${cellTone(widget, cell.column.id, cell.getValue())}`}>{result.row_kinds?.[row.index] === 'subtotal' && cell.column.id === result.columns[0] ? 'Subtotal · ' : ''}{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody>
        {Object.keys(result.totals).length ? <tfoot><tr className="border-t bg-muted/40 font-medium">{result.columns.map((field, i) => <td key={field} className="p-2">{result.totals[field] == null ? i === 0 ? 'Total' : '—' : String(result.totals[field])}</td>)}</tr></tfoot> : null}
      </table>
    </div>
    {state && onEvent ? <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span>Page {(state.page_index[result.widget_id] ?? 0) + 1}{Object.keys(result.totals).length ? ' · totals cover all matching rows' : ''}</span><div className="flex gap-1"><Button size="sm" variant="outline" disabled={!state.page_index[result.widget_id]} onClick={() => onEvent({ type: 'TABLE_PAGE', target: result.widget_id, page_index: (state.page_index[result.widget_id] ?? 0) - 1 })}>Previous</Button><Button size="sm" variant="outline" disabled={!result.has_more} onClick={() => onEvent({ type: 'TABLE_PAGE', target: result.widget_id, page_index: (state.page_index[result.widget_id] ?? 0) + 1 })}>Next</Button></div></div> : null}
  </div>
}


function cellTone(widget: Widget | undefined, field: string, value: unknown) {
  if (value == null) return ''
  const rule = widget?.conditional_formats?.find(rule => {
    if (rule.field.replace(/\./g, '__') !== field) return false
    const comparison = compareDecimal(value, rule.value)
    if (comparison == null) return false
    return rule.operator === '<' ? comparison < 0 : rule.operator === '<=' ? comparison <= 0 : rule.operator === '=' ? comparison === 0 : rule.operator === '>=' ? comparison >= 0 : comparison > 0
  })
  return rule?.tone === 'positive' ? 'bg-success/10 text-success-strong' : rule?.tone === 'negative' ? 'bg-destructive/10 text-destructive' : rule?.tone === 'attention' ? 'bg-warning/10 text-warning-strong' : ''
}
