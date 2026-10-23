import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { reportsPath } from './api'
import type { Filter, FilterValue, Interaction, Scalar, ViewState } from './contracts'

export function ReportFilter({ filter, state, disabled, onEvent }: { filter: Filter; state: ViewState; disabled: boolean; onEvent: (event: Interaction) => void }) {
  const epoch = useAuthStore(s => s.securityEpoch)
  const value = state.filters[filter.id] ?? filter.default
  const [text, setText] = useState(filter.kind === 'text' ? String(value.values[0] ?? '') : '')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const categorical = ['single', 'multi'].includes(filter.kind)
  const options = useQuery({
    queryKey: ['report-options', epoch, state.session_id, state.state_revision, filter.id, search, offset],
    queryFn: ({ signal }) => api.get<{ values: Scalar[]; next_offset: number | null }>(`${reportsPath}/${state.report_id}/filter-options/${filter.id}?view_session_id=${state.session_id}&search=${encodeURIComponent(search)}&offset=${offset}`, signal),
    enabled: categorical, retry: false, gcTime: 0,
  })
  const set = (next: Partial<FilterValue>) => onEvent({ type: 'FILTER_SET', target: filter.id, value: { kind: 'unset', values: [], minimum: null, maximum: null, relative: null, ...next } })
  const id = `${state.session_id}-${filter.id}`
  const selected = value.values.map(v => JSON.stringify(v))
  const choices = [...new Map([...value.values, ...(options.data?.values ?? [])].map(v => [JSON.stringify(v), v])).values()]
  return <fieldset disabled={disabled} className="min-w-0 space-y-1.5">
    <Label htmlFor={id}>{filter.label}{filter.required ? ' *' : ''}</Label>
    {categorical ? <>
      <form className="flex gap-1" onSubmit={e => { e.preventDefault(); setSearch(text); setOffset(0) }}><Input aria-label={`Search ${filter.label}`} placeholder="Find a value" value={text} onChange={e => setText(e.target.value)} /><Button type="submit" variant="outline" size="sm">Find</Button></form>
      <select id={id} multiple={filter.kind === 'multi'} className="min-h-9 w-full rounded-md border border-input bg-background px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" value={filter.kind === 'multi' ? selected : selected[0] ?? ''} onChange={e => {
        const values = Array.from(e.target.selectedOptions).filter(o => o.value !== '').map(o => JSON.parse(o.value) as Scalar)
        set({ kind: values.length || filter.kind === 'multi' ? filter.kind as 'single' | 'multi' : 'unset', values })
      }}>
        {filter.kind === 'single' ? <option value="" disabled={filter.required}>All values</option> : null}
        {choices.map(v => <option key={JSON.stringify(v)} value={JSON.stringify(v)}>{v === null ? '(Blank)' : String(v)}</option>)}
      </select>
      {options.isPending ? <p role="status" className="text-xs text-muted-foreground">Loading values…</p> : options.isError ? <p role="alert" className="text-xs text-destructive">Values unavailable under current access.</p> : <div className="flex gap-2 text-xs"><button type="button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 100))} className="text-muted-foreground underline disabled:opacity-40">Previous values</button><button type="button" disabled={!options.data?.next_offset} onClick={() => setOffset(options.data!.next_offset!)} className="text-muted-foreground underline disabled:opacity-40">More values</button></div>}
    </> : filter.kind === 'boolean' ? <select id={id} className="min-h-9 w-full rounded-md border bg-background px-2 text-sm" value={value.kind === 'unset' ? '' : String(value.values[0])} onChange={e => set(e.target.value === '' ? {} : { kind: 'single', values: [e.target.value === 'true'] })}><option value="" disabled={filter.required}>All values</option><option value="true">Yes</option><option value="false">No</option></select> : filter.kind === 'text' ?
      <form className="flex gap-1" onSubmit={e => { e.preventDefault(); set(text ? { kind: 'single', values: [text] } : {}) }}><Input id={id} value={text} placeholder={String(value.values[0] ?? 'Contains text')} onChange={e => setText(e.target.value)} /><Button variant="outline" type="submit" size="sm">Apply</Button></form> :
      <form className="space-y-1" onSubmit={e => {
        e.preventDefault(); const data = new FormData(e.currentTarget)
        const parse = (key: string): Scalar => data.get(key) ? String(data.get(key)) : null
        set({ kind: filter.kind as 'range' | 'date_range', minimum: parse('minimum'), maximum: parse('maximum') })
      }}><Input id={id} name="minimum" aria-label={`${filter.label} start`} type={filter.kind === 'date_range' ? 'date' : 'number'} step="any" defaultValue={value.minimum == null ? '' : String(value.minimum)} /><Input name="maximum" aria-label={`${filter.label} end${filter.kind === 'date_range' ? ' (exclusive)' : ''}`} type={filter.kind === 'date_range' ? 'date' : 'number'} step="any" defaultValue={value.maximum == null ? '' : String(value.maximum)} /><Button variant="outline" type="submit" size="sm">Apply range</Button>{filter.kind === 'date_range' ? <><p className="text-xs text-muted-foreground">The end date is excluded.</p><label className="block text-xs text-muted-foreground">Relative period<select aria-label={`${filter.label} relative period`} className="mt-1 min-h-9 w-full rounded-md border bg-background px-2 text-sm" value={value.relative ?? ''} onChange={e => e.target.value && set({ kind: 'date_range', relative: e.target.value })}><option value="">Choose a relative period</option>{['today', 'yesterday', 'this_week', 'last_week', 'this_month', 'last_month', 'this_quarter', 'last_quarter', 'this_year', 'last_year'].map(period => <option key={period} value={period}>{period.replace(/_/g, ' ')}</option>)}</select></label></> : null}</form>}
    <button type="button" className="rounded-sm text-xs text-muted-foreground underline focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onEvent({ type: 'FILTER_RESET', target: filter.id })}>Reset {filter.label}</button>
  </fieldset>
}
