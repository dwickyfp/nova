import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import type { CustomTool } from '@/features/agents/api'

export type CustomToolDraft = {
  name: string
  description: string
  database_name: string
  parameters: { name: string; type: string; description: string; required: boolean }[]
  statements: string[]
  /** 'result' returns rows to the agent; 'run' executes with no return. */
  output_mode: 'result' | 'run'
}

const PARAM_TYPES = ['string', 'int', 'float', 'boolean', 'date', 'datetime', 'decimal', 'array', 'identifier']

function emptyDraft(): CustomToolDraft {
  return {
    name: '',
    description: '',
    database_name: '',
    parameters: [],
    statements: [''],
    output_mode: 'result',
  }
}

/**
 * Custom tool editor.
 *
 * A Nova-side SQL procedure: a name, a list of typed parameters, one or more
 * SQL statements that reference the parameters as ``{{name}}``, and an output
 * mode. This is the procedure layer Nova needs because StarRocks has no
 * callable stored procedure. The call signature is shown live above the
 * parameters, so what the agent will call stays explicit.
 */
export function CustomToolEditorDialog({
  open,
  onOpenChange,
  initial,
  onSubmit,
  submitting,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initial?: CustomTool | null
  onSubmit: (draft: CustomToolDraft) => void
  submitting: boolean
}) {
  const [draft, setDraft] = useState<CustomToolDraft>(() =>
    initial
      ? {
          name: initial.name,
          description: initial.description,
          database_name: initial.database_name ?? '',
          parameters: (initial.definition.parameters ?? []).map((p) => ({
            name: p.name,
            type: p.type ?? 'string',
            description: p.description ?? '',
            required: p.required ?? true,
          })),
          statements:
            initial.definition.statements?.length ? initial.definition.statements : [''],
          output_mode: initial.definition.output_mode ?? 'result',
        }
      : emptyDraft()
  )

  const setParam = (
    index: number,
    patch: Partial<CustomToolDraft['parameters'][number]>
  ) =>
    setDraft((d) => ({
      ...d,
      parameters: d.parameters.map((p, i) => (i === index ? { ...p, ...patch } : p)),
    }))

  const addParam = () =>
    setDraft((d) => ({
      ...d,
      parameters: [
        ...d.parameters,
        { name: '', type: 'string', description: '', required: true },
      ],
    }))

  const removeParam = (index: number) =>
    setDraft((d) => ({
      ...d,
      parameters: d.parameters.filter((_, i) => i !== index),
    }))

  const setStatement = (index: number, value: string) =>
    setDraft((d) => ({
      ...d,
      statements: d.statements.map((s, i) => (i === index ? value : s)),
    }))

  const addStatement = () =>
    setDraft((d) => ({ ...d, statements: [...d.statements, ''] }))

  const removeStatement = (index: number) =>
    setDraft((d) => ({
      ...d,
      statements: d.statements.filter((_, i) => i !== index),
    }))

  const parameterNames = draft.parameters.map((p) => p.name.trim())
  const validParameters = draft.parameters.every(
    (p) => /^[A-Za-z_][A-Za-z0-9_$]*$/.test(p.name.trim()) && Boolean(p.description.trim())
  ) && new Set(parameterNames).size === parameterNames.length
  const canSubmit =
    /^[A-Za-z_][A-Za-z0-9_$]*$/.test(draft.name.trim()) &&
    Boolean(draft.description.trim()) &&
    draft.statements.some((s) => s.trim()) &&
    validParameters

  // How the agent will call this tool, kept in view while it is written.
  const signature = `${draft.name.trim() || 'TOOL_NAME'}(${draft.parameters
    .map((p) => `${p.name.trim() || '…'}${p.required ? '' : '?'}: ${p.type}`)
    .join(', ')})`

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Header and actions stay put; only the form scrolls. */}
      <DialogContent className='flex max-h-[88vh] flex-col gap-0 p-0 sm:max-w-2xl'>
        <DialogHeader className='shrink-0 border-b px-6 pt-6 pb-4 text-start'>
          <DialogTitle>{initial ? 'Edit custom tool' : 'New custom tool'}</DialogTitle>
          <DialogDescription>
            SQL the agent can run with arguments, under the caller&apos;s own access
          </DialogDescription>
        </DialogHeader>

        <div className='min-h-0 flex-1 space-y-8 overflow-y-auto px-6 py-6'>
          <section aria-labelledby='ct-about' className='space-y-4'>
            <h3 id='ct-about' className='text-sm font-medium'>
              About
            </h3>
            <div className='grid gap-4 sm:grid-cols-2'>
              <Field
                id='ct-name'
                label='Name'
                hint='How the agent refers to this tool'
              >
                <Input
                  id='ct-name'
                  value={draft.name}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      name: e.target.value.toUpperCase().replace(/\s+/g, '_'),
                    }))
                  }
                  placeholder='MONTHLY_REVENUE'
                  className='font-mono'
                />
              </Field>
              <Field
                id='ct-db'
                label='Default database'
                optional
                hint='For statements that do not name one'
              >
                <Input
                  id='ct-db'
                  value={draft.database_name}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, database_name: e.target.value }))
                  }
                  placeholder='sales'
                  className='font-mono'
                />
              </Field>
            </div>
            <Field
              id='ct-desc'
              label='Description'
              hint='The agent reads this to decide when to call the tool: say what it returns'
            >
              <Textarea
                id='ct-desc'
                value={draft.description}
                onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
                placeholder='Revenue per month for a date range, by region'
                className='min-h-20'
              />
            </Field>
            <fieldset className='space-y-2'>
              <legend className='text-sm font-medium'>The agent receives</legend>
              <RadioGroup
                value={draft.output_mode}
                onValueChange={(v) =>
                  setDraft((d) => ({
                    ...d,
                    output_mode: v as CustomToolDraft['output_mode'],
                  }))
                }
                className='grid gap-2 sm:grid-cols-2'
              >
                {OUTPUT_MODES.map((mode) => (
                  <label
                    key={mode.value}
                    htmlFor={`ct-output-${mode.value}`}
                    className='flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/40 has-[[data-state=checked]]:border-primary/60 has-[[data-state=checked]]:bg-muted/40'
                  >
                    <RadioGroupItem
                      id={`ct-output-${mode.value}`}
                      value={mode.value}
                      className='mt-0.5'
                    />
                    <span className='min-w-0'>
                      <span className='block text-sm font-medium'>{mode.label}</span>
                      <span className='mt-0.5 block text-xs text-muted-foreground'>
                        {mode.hint}
                      </span>
                    </span>
                  </label>
                ))}
              </RadioGroup>
            </fieldset>
          </section>

          <section aria-labelledby='ct-params' className='space-y-3'>
            <div className='flex items-center justify-between gap-3'>
              <h3 id='ct-params' className='text-sm font-medium'>
                Parameters
              </h3>
              <Button variant='ghost' size='sm' onClick={addParam}>
                <Plus className='size-4' />
                Add parameter
              </Button>
            </div>
            <p className='truncate rounded-md bg-muted/60 px-3 py-2 font-mono text-xs text-muted-foreground'>
              {signature}
            </p>
            {draft.parameters.length === 0 ? (
              <p className='text-xs text-muted-foreground'>
                No parameters: the tool takes no arguments
              </p>
            ) : (
              <ul className='divide-y rounded-lg border'>
                {draft.parameters.map((param, i) => {
                  const label = param.name || `parameter ${i + 1}`
                  return (
                    <li key={i} className='space-y-2 p-3'>
                      <div className='flex flex-wrap items-center gap-2'>
                        <Input
                          aria-label={`Parameter ${i + 1} name`}
                          value={param.name}
                          onChange={(e) =>
                            setParam(i, { name: e.target.value.replace(/\s+/g, '_') })
                          }
                          placeholder='start_date'
                          className='h-8 min-w-0 flex-1 basis-40 font-mono text-xs'
                        />
                        <Select
                          value={param.type}
                          onValueChange={(v) => setParam(i, { type: v })}
                        >
                          <SelectTrigger
                            aria-label={`Type of ${label}`}
                            className='h-8 w-32 font-mono text-xs'
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {PARAM_TYPES.map((t) => (
                              <SelectItem key={t} value={t} className='font-mono text-xs'>
                                {t}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <label className='flex h-8 items-center gap-2 px-1 text-xs text-muted-foreground'>
                          <Switch
                            checked={param.required}
                            onCheckedChange={(v) => setParam(i, { required: v })}
                            aria-label={`${label} is required`}
                          />
                          Required
                        </label>
                        <Button
                          size='icon'
                          variant='ghost'
                          className='size-8 text-muted-foreground'
                          onClick={() => removeParam(i)}
                          aria-label={`Remove parameter ${param.name || i + 1}`}
                        >
                          <Trash2 className='size-4' />
                        </Button>
                      </div>
                      <Input
                        aria-label={`Parameter ${i + 1} description`}
                        value={param.description}
                        onChange={(e) => setParam(i, { description: e.target.value })}
                        placeholder='What the value is, so the agent can supply it'
                        className='h-8 text-xs'
                      />
                    </li>
                  )
                })}
              </ul>
            )}
          </section>

          <section aria-labelledby='ct-sql' className='space-y-3'>
            <div className='flex items-center justify-between gap-3'>
              <h3 id='ct-sql' className='text-sm font-medium'>
                SQL
              </h3>
              <Button variant='ghost' size='sm' onClick={addStatement}>
                <Plus className='size-4' />
                Add statement
              </Button>
            </div>
            <p className='text-xs text-muted-foreground'>
              Statements run top to bottom. Use a parameter as{' '}
              <code className='rounded border bg-muted px-1 py-0.5 font-mono'>
                {'{{name}}'}
              </code>
            </p>
            <ol className='space-y-2'>
              {draft.statements.map((statement, i) => (
                <li key={i} className='flex items-start gap-2'>
                  <span
                    aria-hidden='true'
                    className='mt-2 w-5 shrink-0 text-right font-mono text-xs text-muted-foreground tabular-nums'
                  >
                    {i + 1}
                  </span>
                  <Textarea
                    aria-label={`Statement ${i + 1}`}
                    value={statement}
                    onChange={(e) => setStatement(i, e.target.value)}
                    placeholder='SELECT … WHERE order_date >= {{start_date}}'
                    className='min-h-24 font-mono text-xs'
                  />
                  {draft.statements.length > 1 ? (
                    <Button
                      size='icon'
                      variant='ghost'
                      className='size-8 shrink-0 text-muted-foreground'
                      onClick={() => removeStatement(i)}
                      aria-label={`Remove statement ${i + 1}`}
                    >
                      <Trash2 className='size-4' />
                    </Button>
                  ) : null}
                </li>
              ))}
            </ol>
          </section>
        </div>

        <DialogFooter className='shrink-0 border-t px-6 py-4'>
          <Button variant='ghost' onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canSubmit || submitting} onClick={() => onSubmit(draft)}>
            {initial ? 'Save changes' : 'Create tool'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

const OUTPUT_MODES = [
  {
    value: 'result',
    label: 'The rows it returns',
    hint: 'For a lookup the agent reasons over',
  },
  {
    value: 'run',
    label: 'Only that it ran',
    hint: 'For a write, where the rows are noise',
  },
] as const

/** A labelled form field with an optional hint under it. */
function Field({
  id,
  label,
  hint,
  optional = false,
  children,
}: {
  id: string
  label: string
  hint?: string
  optional?: boolean
  children: React.ReactNode
}) {
  return (
    <div className='space-y-1.5'>
      <Label htmlFor={id} className='flex items-baseline gap-1.5'>
        {label}
        {optional ? (
          <span className='text-xs font-normal text-muted-foreground'>Optional</span>
        ) : null}
      </Label>
      {children}
      {hint ? <p className='text-xs text-muted-foreground'>{hint}</p> : null}
    </div>
  )
}
