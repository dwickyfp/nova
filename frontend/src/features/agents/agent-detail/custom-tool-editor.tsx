import { useState } from 'react'
import { GripVertical, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
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

const PARAM_TYPES = ['string', 'int', 'float', 'boolean', 'date', 'datetime', 'decimal']

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
 * callable stored procedure. Parameters are shown as a table so the agent's
 * call signature is explicit.
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

  // A parameter with no name cannot be referenced as {{name}}, so it blocks
  // submission rather than silently dropping the argument at run time.
  const unnamedParameter = draft.parameters.some((p) => !p.name.trim())
  const canSubmit =
    Boolean(draft.name.trim()) &&
    draft.statements.some((s) => s.trim()) &&
    !unnamedParameter

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='max-h-[90vh] max-w-2xl overflow-y-auto'>
        <DialogHeader>
          <DialogTitle>{initial ? 'Edit custom tool' : 'New custom tool'}</DialogTitle>
        </DialogHeader>

        <section className='space-y-4'>
          <div className='grid gap-4 sm:grid-cols-2'>
            <div className='space-y-1.5'>
              <Label htmlFor='ct-name'>Name</Label>
              <Input
                id='ct-name'
                value={draft.name}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    name: e.target.value.toUpperCase().replace(/\s+/g, '_'),
                  }))
                }
                placeholder='UPPER_SNAKE_CASE'
                className='font-mono'
              />
              <p className='text-xs text-muted-foreground'>
                How the agent refers to this tool.
              </p>
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='ct-db'>Default database</Label>
              <Input
                id='ct-db'
                value={draft.database_name}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, database_name: e.target.value }))
                }
                placeholder='fqn.of.your.database'
              />
              <p className='text-xs text-muted-foreground'>
                Used when a statement is not fully qualified.
              </p>
            </div>
          </div>

          <div className='space-y-1.5'>
            <Label htmlFor='ct-desc'>Description</Label>
            <Textarea
              id='ct-desc'
              value={draft.description}
              onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
              placeholder='What this tool returns, and when the agent should reach for it.'
              className='min-h-16'
            />
            <p className='text-xs text-muted-foreground'>
              The agent reads this to decide when to call the tool, so state the
              result, not the mechanism.
            </p>
          </div>

          <div className='space-y-1.5'>
            <Label htmlFor='ct-output'>What should the agent receive?</Label>
            <Select
              value={draft.output_mode}
              onValueChange={(v) =>
                setDraft((d) => ({
                  ...d,
                  output_mode: v as CustomToolDraft['output_mode'],
                }))
              }
            >
              <SelectTrigger id='ct-output' className='max-w-sm'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='result'>The rows it returns</SelectItem>
                <SelectItem value='run'>Only that it ran</SelectItem>
              </SelectContent>
            </Select>
            <p className='text-xs text-muted-foreground'>
              {draft.output_mode === 'result'
                ? 'Use this when the agent should reason over the result.'
                : 'Use this for a write, where the result set is noise.'}
            </p>
          </div>
        </section>

        <section className='space-y-3'>
          <div className='flex items-baseline justify-between gap-3'>
            <h3 className='text-sm font-medium'>Parameters</h3>
            <p className='text-xs text-muted-foreground'>
              Reference one in a statement as{' '}
              <code className='rounded bg-muted px-1 font-mono'>{'{{name}}'}</code>
            </p>
          </div>

          {draft.parameters.length === 0 ? (
            <p className='rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground'>
              No parameters. The tool takes no arguments.
            </p>
          ) : (
            <div className='space-y-2'>
              {draft.parameters.map((param, i) => (
                <div key={i} className='rounded-lg border p-3'>
                  <div className='grid gap-3 sm:grid-cols-[minmax(0,1fr)_130px_auto]'>
                    <div className='space-y-1.5'>
                      <Label className='text-xs'>Name</Label>
                      <Input
                        value={param.name}
                        onChange={(e) =>
                          setParam(i, {
                            name: e.target.value.replace(/\s+/g, '_'),
                          })
                        }
                        placeholder='parameter_name'
                        className='font-mono text-xs'
                      />
                    </div>
                    <div className='space-y-1.5'>
                      <Label className='text-xs'>Type</Label>
                      <Select
                        value={param.type}
                        onValueChange={(v) => setParam(i, { type: v })}
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {PARAM_TYPES.map((t) => (
                            <SelectItem key={t} value={t}>
                              {t}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <Button
                      size='icon'
                      variant='ghost'
                      className='mt-6'
                      onClick={() => removeParam(i)}
                      aria-label={`Remove parameter ${param.name || i + 1}`}
                    >
                      <Trash2 className='size-4' />
                    </Button>
                  </div>
                  <div className='mt-3 space-y-1.5'>
                    <Label className='text-xs'>Description</Label>
                    <Input
                      value={param.description}
                      onChange={(e) => setParam(i, { description: e.target.value })}
                      placeholder='What the value is, so the agent can supply it'
                      className='text-sm'
                    />
                  </div>
                  <label className='mt-3 flex items-center gap-2 text-sm'>
                    <Checkbox
                      checked={param.required}
                      onCheckedChange={(v) => setParam(i, { required: Boolean(v) })}
                    />
                    Required
                  </label>
                </div>
              ))}
            </div>
          )}

          <Button variant='outline' size='sm' onClick={addParam}>
            <Plus className='size-4' />
            Add parameter
          </Button>
        </section>

        <section className='space-y-3'>
          <div className='flex items-baseline justify-between gap-3'>
            <h3 className='text-sm font-medium'>Statements</h3>
            <p className='text-xs text-muted-foreground'>Run top to bottom.</p>
          </div>
          <div className='space-y-2'>
            {draft.statements.map((statement, i) => (
              <div key={i} className='flex items-start gap-2'>
                <GripVertical className='mt-3 size-4 shrink-0 text-muted-foreground' />
                <Textarea
                  value={statement}
                  onChange={(e) => setStatement(i, e.target.value)}
                  placeholder='SELECT … WHERE created_at >= {{start_date}}'
                  className='min-h-20 font-mono text-xs'
                />
                <Button
                  size='icon'
                  variant='ghost'
                  className='mt-1'
                  disabled={draft.statements.length === 1}
                  onClick={() => removeStatement(i)}
                  aria-label={`Remove statement ${i + 1}`}
                >
                  <Trash2 className='size-4' />
                </Button>
              </div>
            ))}
          </div>
          <Button variant='outline' size='sm' onClick={addStatement}>
            <Plus className='size-4' />
            Add statement
          </Button>
        </section>

        <DialogFooter>
          <Button variant='outline' onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canSubmit || submitting} onClick={() => onSubmit(draft)}>
            {initial ? 'Update' : 'Create tool'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
