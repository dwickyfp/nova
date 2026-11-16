import { useMemo, useState } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useAuthStore } from '@/stores/auth-store'
import type { ChartDefinition, DashboardDefinition, DatasetDefinition, Interaction } from '../contracts'
import { useDatasetDefinition } from '../charts/chart-data'
import { humanize } from '../charts/chart-query'
import { drillDimension } from '../charts/drill'
import { nativeDocuments } from '../documents'
import { useDashboards } from './hooks'

const GROUPABLE = new Set(['string', 'boolean', 'date', 'timestamp', 'integer'])

/**
 * Link a chart to another dashboard: clicking a value opens that dashboard
 * narrowed to the same value on the field chosen here.
 */
export function LinkDialog({ open, onOpenChange, dashboardId, widgetId, chart, existing, onSave, saving }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  dashboardId: string
  widgetId: string
  chart: ChartDefinition
  existing?: Interaction
  onSave: (link: Interaction | null) => void
  saving: boolean
}) {
  const epoch = useAuthStore(state => state.securityEpoch)
  const { entries } = useDashboards()
  const source = drillDimension(chart)
  const sourceDataset = useDatasetDefinition(chart.dataset_id, chart.dataset_revision)
  const sourceName = sourceDataset.data?.fields.find(item => item.field_id === source?.field_id)?.name
  const [target, setTarget] = useState(existing?.target_dashboard_id ?? '')
  const [field, setField] = useState(existing?.mappings?.[0] ? JSON.stringify(existing.mappings[0].target) : '')
  const [label, setLabel] = useState(existing?.label ?? '')
  const choices = entries.filter(entry => entry.resource_id !== dashboardId)
  const chosen = choices.find(entry => entry.resource_id === target)

  const definition = useQuery({
    queryKey: ['native-dataviz', 'link-target', epoch, target, chosen?.editable],
    queryFn: async ({ signal }) => (await nativeDocuments(epoch).read('dashboards', target, { draft: Boolean(chosen?.editable), signal })).definition as DashboardDefinition,
    enabled: open && Boolean(chosen), retry: false,
  })
  const datasets = useQueries({
    queries: (definition.data?.datasets ?? []).map(reference => ({
      queryKey: ['native-dataviz', 'dataset-definition', epoch, reference.dataset_id, reference.revision],
      queryFn: async ({ signal }: { signal: AbortSignal }) =>
        (await nativeDocuments(epoch).read('datasets', reference.dataset_id, { draft: Boolean(chosen?.editable), revision: reference.revision, signal })).definition as DatasetDefinition,
      enabled: open, retry: false, staleTime: Infinity,
    })),
  })
  const fields = useMemo(() => (definition.data?.datasets ?? []).flatMap((reference, index) => {
    const dataset = datasets[index]?.data
    return (dataset?.fields ?? [])
      .filter(item => item.semantic_role === 'dimension' && GROUPABLE.has(item.logical_type))
      .map(item => ({
        binding: { dataset_id: reference.dataset_id, dataset_revision: reference.revision, field_id: item.field_id },
        label: `${humanize(item.name)}${(definition.data?.datasets?.length ?? 0) > 1 ? ` · ${dataset?.title ?? ''}` : ''}`,
      }))
  }), [definition.data, datasets])

  const save = () => {
    if (!source || !chosen || !field) return
    onSave({
      action_id: existing?.action_id ?? crypto.randomUUID(),
      kind: 'navigate', source_widget_id: widgetId, run_on: 'menu',
      target_dashboard_id: chosen.resource_id, target_workspace_id: chosen.workspace_id,
      label: label.trim() || `Open ${chosen.title}`,
      mappings: [{
        source: { dataset_id: chart.dataset_id, dataset_revision: chart.dataset_revision, field_id: source.field_id },
        target: JSON.parse(field), transform: 'identity',
      }],
    })
  }

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className='sm:max-w-md'>
      <DialogHeader>
        <DialogTitle>Link to a dashboard</DialogTitle>
        <DialogDescription>
          Clicking a value in this chart offers to open the other dashboard, narrowed to that value.
          People only see it if they can open that dashboard.
        </DialogDescription>
      </DialogHeader>
      {!source ? <p className='text-sm text-muted-foreground'>This chart has no single grouping to link from.</p> : <div className='space-y-4'>
        <div className='space-y-1.5'>
          <Label htmlFor='link-target' className='text-xs text-muted-foreground'>Dashboard to open</Label>
          <Select value={target} onValueChange={value => { setTarget(value); setField('') }}>
            <SelectTrigger id='link-target' className='w-full'><SelectValue placeholder='Choose a dashboard' /></SelectTrigger>
            <SelectContent>{choices.map(entry => <SelectItem key={entry.resource_id} value={entry.resource_id}>{entry.title}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className='space-y-1.5'>
          <Label htmlFor='link-field' className='text-xs text-muted-foreground'>Narrow it by</Label>
          <Select value={field} onValueChange={setField} disabled={!chosen || definition.isPending}>
            <SelectTrigger id='link-field' className='w-full'>
              <SelectValue placeholder={!chosen ? 'Choose a dashboard first' : definition.isError ? 'That dashboard cannot be read' : 'Choose a field'} />
            </SelectTrigger>
            <SelectContent>{fields.map(item => <SelectItem key={JSON.stringify(item.binding)} value={JSON.stringify(item.binding)}>{item.label}</SelectItem>)}</SelectContent>
          </Select>
          <p className='text-xs text-muted-foreground'>
            {sourceName ? `Its values should match this chart's ${humanize(sourceName).toLowerCase()} values.` : 'Its values should match the values this chart groups by.'}
          </p>
        </div>
        <div className='space-y-1.5'>
          <Label htmlFor='link-label' className='text-xs text-muted-foreground'>Menu label</Label>
          <Input id='link-label' value={label} maxLength={120} placeholder={chosen ? `Open ${chosen.title}` : 'Open the linked dashboard'}
            onChange={event => setLabel(event.target.value)} />
        </div>
      </div>}
      <DialogFooter className='gap-2 sm:justify-between'>
        {existing ? <Button variant='ghost' className='text-destructive' disabled={saving} onClick={() => onSave(null)}>Remove link</Button> : <span />}
        <div className='flex gap-2'>
          <Button variant='outline' onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!source || !chosen || !field || saving} onClick={save}>{saving ? 'Saving…' : 'Save link'}</Button>
        </div>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
