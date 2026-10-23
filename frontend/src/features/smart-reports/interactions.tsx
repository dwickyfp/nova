import { useState } from 'react'
import { ReportDetail } from './detail'
import { Button } from '@/components/ui/button'
import type { Interaction, Scalar, ViewState, Widget, WidgetResult } from './contracts'

export function WidgetInteractions({ widget, result, state, field, onEvent }: { widget: Widget; result: WidgetResult; state: ViewState; field?: string; onEvent: (event: Interaction) => void }) {
  const [chosen, setChosen] = useState<Scalar[]>([])
  const [dimension, setDimension] = useState(widget.drill_dimensions[0] ?? '')
  const grouping = state.group_by[widget.id] ?? widget.query?.dimensions ?? []
  const selection = field ?? grouping[grouping.length - 1]
  const index = result.columns.findIndex(c => c === selection || c === selection?.replace(/\./g, '__'))
  const choices = [...new Map(result.rows.map(row => [JSON.stringify(row[index]), row[index]])).values()]
  const path = state.drill_paths[widget.id] ?? []
  return <div className="mt-3 space-y-2">
    {path.length ? <div className="flex flex-wrap items-center gap-2 text-xs"><span aria-label="Drill path">{path.map(p => `${p.field}: ${Array.isArray(p.value) ? p.value.join(', ') : p.value}`).join(' → ')}</span><Button size="sm" variant="ghost" onClick={() => onEvent({ type: 'DRILL_UP', target: widget.id })}>Back one level</Button></div> : null}
    {index >= 0 && (field || widget.drill_dimensions.length || widget.detail_fields?.length) ? <details><summary className="cursor-pointer text-xs text-muted-foreground">Select values to explore</summary><fieldset className="mt-2 max-h-40 space-y-1 overflow-auto rounded-md border p-2"><legend className="px-1 text-xs">{selection}</legend>{choices.map(value => <label key={JSON.stringify(value)} className="flex min-h-9 items-center gap-2 text-sm"><input type="checkbox" checked={chosen.some(v => JSON.stringify(v) === JSON.stringify(value))} onChange={e => setChosen(values => e.target.checked ? [...values, value] : values.filter(v => JSON.stringify(v) !== JSON.stringify(value)))} />{String(value ?? '(Blank)')}</label>)}</fieldset><div className="mt-2 flex flex-wrap gap-2">{field ? <Button size="sm" variant="outline" disabled={!chosen.length} onClick={() => onEvent({ type: 'CROSS_FILTER_APPLY', target: widget.id, values: chosen })}>Filter related widgets</Button> : null}{widget.drill_dimensions.length ? <><select value={dimension} onChange={e => setDimension(e.target.value)} aria-label={`Drill ${widget.title} into`} className="min-h-9 rounded-md border bg-background px-2 text-sm">{widget.drill_dimensions.map(d => <option key={d}>{d}</option>)}</select><Button size="sm" variant="outline" disabled={!chosen.length || !dimension} onClick={() => { onEvent({ type: 'DRILL_DOWN', target: widget.id, field: dimension, values: chosen }); setChosen([]) }}>Drill into selection</Button><Button size="sm" variant="ghost" disabled={!dimension} onClick={() => onEvent({ type: 'DRILL_BY', target: widget.id, field: dimension })}>Group by dimension</Button></> : null}{selection ? <ReportDetail widget={widget} state={state} field={selection} values={chosen} /> : null}</div></details> : null}
  </div>
}
