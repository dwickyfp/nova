import { useId } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ParameterScalarInput } from './dataset-parameter-inputs'
import { MAX_PARAMETERS, newDeclaration, TYPE_LABELS, type DeclaredType, type DeclarationDraft,
  type WhenOmitted } from './parameter-values'

export function DatasetParameterDeclarations({ drafts, disabled, onChange }: {
  drafts: DeclarationDraft[]
  disabled?: boolean
  onChange: (drafts: DeclarationDraft[]) => void
}) {
  const headingId = useId()
  function update(key: string, changes: Partial<DeclarationDraft>) {
    onChange(drafts.map(draft => draft.key === key ? { ...draft, ...changes } : draft))
  }
  return <section aria-labelledby={headingId} className='min-w-0 space-y-3 rounded-md border p-3'>
    <div className='space-y-1'>
      <h4 id={headingId} className='text-sm font-medium'>Parameters</h4>
      <p className='text-xs text-muted-foreground'>
        Declare each named value the query uses, such as <code className='font-mono'>:region</code>. You supply the values when you preview the dataset.
      </p>
    </div>
    {drafts.map((draft, index) => <DeclarationRow key={draft.key} draft={draft} position={index + 1}
      disabled={disabled} onChange={changes => update(draft.key, changes)}
      onRemove={() => onChange(drafts.filter(item => item.key !== draft.key))} />)}
    <Button type='button' variant='outline' className='h-11 sm:h-9' disabled={disabled || drafts.length >= MAX_PARAMETERS}
      onClick={() => onChange([...drafts, newDeclaration()])}><Plus />Add parameter</Button>
  </section>
}

function DeclarationRow({ draft, position, disabled, onChange, onRemove }: {
  draft: DeclarationDraft
  position: number
  disabled?: boolean
  onChange: (changes: Partial<DeclarationDraft>) => void
  onRemove: () => void
}) {
  const ids = { name: useId(), type: useId(), omitted: useId(), multiple: useId(), precision: useId(), scale: useId() }
  const label = draft.name.trim() || `Parameter ${position}`
  return <fieldset disabled={disabled} className='min-w-0 space-y-3 border-t pt-3'>
    <legend className='sr-only'>{label}</legend>
    <div className='grid min-w-0 gap-3 sm:grid-cols-2'>
      <div className='min-w-0 space-y-1'>
        <Label htmlFor={ids.name}>Parameter name</Label>
        <Input id={ids.name} value={draft.name} maxLength={64} autoComplete='off' spellCheck={false}
          className='h-11 font-mono sm:h-9' onChange={event => onChange({ name: event.target.value })} />
      </div>
      <div className='min-w-0 space-y-1'>
        <Label htmlFor={ids.type}>Type</Label>
        <Select value={draft.type} disabled={disabled}
          onValueChange={type => onChange({ type: type as DeclaredType, defaultValue: '' })}>
          <SelectTrigger id={ids.type} className='h-11 w-full sm:h-9'><SelectValue /></SelectTrigger>
          <SelectContent>{(Object.keys(TYPE_LABELS) as DeclaredType[]).map(type =>
            <SelectItem key={type} value={type}>{TYPE_LABELS[type]}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      {draft.type === 'decimal' && <div className='grid min-w-0 grid-cols-2 gap-3 sm:col-span-2'>
        <div className='min-w-0 space-y-1'>
          <Label htmlFor={ids.precision}>Precision</Label>
          <Input id={ids.precision} value={draft.precision} inputMode='numeric' maxLength={2}
            className='h-11 sm:h-9' onChange={event => onChange({ precision: event.target.value })} />
        </div>
        <div className='min-w-0 space-y-1'>
          <Label htmlFor={ids.scale}>Scale</Label>
          <Input id={ids.scale} value={draft.scale} inputMode='numeric' maxLength={2}
            className='h-11 sm:h-9' onChange={event => onChange({ scale: event.target.value })} />
        </div>
      </div>}
      <div className='min-w-0 space-y-1'>
        <Label htmlFor={ids.omitted}>When no value is supplied</Label>
        <Select value={draft.omitted} disabled={disabled}
          onValueChange={omitted => onChange({ omitted: omitted as WhenOmitted })}>
          <SelectTrigger id={ids.omitted} className='h-11 w-full sm:h-9'><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value='required'>Require a value</SelectItem>
            <SelectItem value='default'>Use a default value</SelectItem>
            <SelectItem value='null'>Use NULL</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {draft.omitted === 'default' && <ParameterScalarInput label='Default value' value={draft.defaultValue}
        disabled={disabled} parameter={{ parameter_id: draft.name || 'value', type: draft.type }}
        onChange={defaultValue => onChange({ defaultValue })} />}
    </div>
    <div className='flex flex-wrap items-center justify-between gap-2'>
      <div className='flex min-h-11 items-center gap-2 sm:min-h-9'>
        <Checkbox id={ids.multiple} checked={draft.multiple} disabled={disabled}
          onCheckedChange={checked => onChange({ multiple: checked === true })} />
        <Label htmlFor={ids.multiple} className='font-normal'>Allow several values, for use with IN (…)</Label>
      </div>
      <Button type='button' variant='ghost' className='h-11 sm:h-9' disabled={disabled}
        aria-label={`Remove ${label}`} onClick={onRemove}><Trash2 />Remove</Button>
    </div>
  </fieldset>
}
