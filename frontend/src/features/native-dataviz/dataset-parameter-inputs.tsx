import { useId } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { Parameter } from './contracts'
import type { ParameterDraft } from './parameter-values'

export function ParameterScalarInput({ parameter, value, label, disabled, onChange }: {
  parameter: Parameter
  value: string
  label: string
  disabled?: boolean
  onChange: (value: string) => void
}) {
  const id = useId()
  const choices = parameter.type === 'boolean' ? ['false', 'true']
    : parameter.allowed?.length ? parameter.allowed.map(item => String(item.value)) : null
  return <div className='min-w-0 flex-1 space-y-1'>
    <Label htmlFor={id}>{label}</Label>
    {choices ? <Select disabled={disabled} value={choices.includes(value) ? String(choices.indexOf(value)) : ''}
      onValueChange={index => onChange(choices[Number(index)])}>
      <SelectTrigger id={id} className='h-11 w-full sm:h-9'><SelectValue placeholder='Choose a value' /></SelectTrigger>
      <SelectContent>{choices.map((choice, index) => <SelectItem key={index} value={String(index)}>
        {choice === '' ? 'Empty text' : choice}
      </SelectItem>)}</SelectContent>
    </Select> : <Input id={id} value={value} disabled={disabled} autoComplete='off' maxLength={4096}
      type={parameter.type === 'date' ? 'date' : 'text'}
      inputMode={parameter.type === 'integer' ? 'numeric' : parameter.type === 'decimal' ? 'decimal' : 'text'}
      className='h-11 min-w-0 sm:h-9' onChange={event => onChange(event.target.value)} />}
    {parameter.type === 'timestamp' && <p className='text-xs text-muted-foreground'>Include a UTC offset, for example 2026-01-01T09:00:00+07:00.</p>}
  </div>
}

export function DatasetParameterInputs({ parameters, drafts, disabled, onChange }: {
  parameters: Parameter[]
  drafts: ParameterDraft[]
  disabled?: boolean
  onChange: (drafts: ParameterDraft[]) => void
}) {
  function update(index: number, changes: Partial<ParameterDraft>) {
    onChange(drafts.map((draft, position) => position === index ? { ...draft, ...changes } : draft))
  }
  if (!parameters.length) return null
  return <div className='max-h-80 shrink-0 space-y-3 overflow-auto rounded-md border p-3' aria-label='Preview parameters'>
    <h4 className='text-sm font-medium'>Preview parameters</h4>
    {parameters.map((parameter, index) => {
      const draft = drafts[index]
      if (!draft) return null
      return <fieldset key={parameter.parameter_id} disabled={disabled} className='min-w-0 space-y-2 border-t pt-2'>
        <legend className='max-w-full break-words px-1 font-mono text-sm'>{parameter.parameter_id}</legend>
        <p className='text-xs text-muted-foreground'>{parameter.type} · {parameter.required === false ? 'Optional' : 'Required'}
          {parameter.multiple ? ' · Multiple values' : ''}</p>
        {parameter.sensitive ? <p role='alert' className='text-sm text-muted-foreground'>Preview is unavailable for sensitive parameters.</p>
          : <>
            <Select value={draft.mode} disabled={disabled} onValueChange={mode => update(index, { mode: mode as ParameterDraft['mode'] })}>
              <SelectTrigger aria-label={`${parameter.parameter_id} input mode`} className='h-11 w-full sm:h-9'><SelectValue /></SelectTrigger>
              <SelectContent>
                {(Boolean(parameter.defaults?.length) || parameter.required === false) && <SelectItem value='default'>
                  {parameter.defaults?.length ? 'Use dataset default' : 'Leave unspecified'}
                </SelectItem>}
                <SelectItem value='value'>Enter value</SelectItem>
                {parameter.nullable && <SelectItem value='null'>NULL</SelectItem>}
              </SelectContent>
            </Select>
            {draft.mode === 'value' && <div className='space-y-2'>
              {draft.values.map((value, valueIndex) => <div key={valueIndex} className='flex items-end gap-2'>
                <ParameterScalarInput parameter={parameter} value={value} disabled={disabled}
                  label={parameter.multiple ? `${parameter.parameter_id} value ${valueIndex + 1}` : `${parameter.parameter_id} value`}
                  onChange={text => update(index, { values: draft.values.map((old, position) => position === valueIndex ? text : old) })} />
                {parameter.multiple && <Button type='button' variant='ghost' size='icon' className='size-11 shrink-0'
                  disabled={disabled || draft.values.length === 1} aria-label={`Remove ${parameter.parameter_id} value ${valueIndex + 1}`}
                  onClick={() => update(index, { values: draft.values.filter((_, position) => position !== valueIndex) })}><Trash2 /></Button>}
              </div>)}
              {parameter.multiple && <Button type='button' variant='outline' className='h-11 sm:h-9'
                disabled={disabled || draft.values.length >= 500} onClick={() => update(index, { values: [...draft.values, ''] })}>
                <Plus />Add {parameter.parameter_id} value
              </Button>}
            </div>}
          </>}
      </fieldset>
    })}
    <p className='text-xs text-muted-foreground'>Inputs apply only to this preview. They do not change the saved dataset.</p>
  </div>
}
