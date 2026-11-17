import { parameterInputSchema, parameterSchema, type Parameter, type ParameterInput } from './contracts'

type Scalar = NonNullable<ParameterInput['values'][number]>

export type ParameterDraft = { mode: 'default' | 'value' | 'null'; values: string[] }

export function initialParameterDrafts(parameters: Parameter[]): ParameterDraft[] {
  return parameters.map(parameter => ({
    mode: parameter.defaults?.length || parameter.required === false ? 'default' : 'value',
    values: [''],
  }))
}

export function parameterScalar(type: Parameter['type'], text: string): Scalar {
  if (type === 'boolean') {
    if (text !== 'true' && text !== 'false') throw new Error('Choose a Boolean value.')
    return { type, value: text === 'true' }
  }
  return { type: type === 'enum' ? 'string' : type, value: text }
}

export function resolveParameterInputs(parameters: Parameter[], drafts: ParameterDraft[]): ParameterInput[] {
  return parameters.flatMap((parameter, index) => {
    if (parameter.sensitive) throw new Error('Preview is unavailable for sensitive parameters.')
    const draft = drafts[index]
    if (!draft) throw new Error('Reload this dataset before entering its parameters.')
    if (draft.mode === 'default') {
      if (!parameter.defaults?.length && parameter.required !== false) {
        throw new Error(`Enter a value for ${parameter.parameter_id}.`)
      }
      return []
    }
    try {
      const values = draft.mode === 'null' ? [null] : draft.values.map(text => parameterScalar(parameter.type, text))
      if (draft.mode === 'null' && !parameter.nullable) throw new Error()
      if (!parameter.multiple && values.length !== 1) throw new Error()
      return [parameterInputSchema.parse({ parameter_id: parameter.parameter_id, values })]
    } catch {
      throw new Error(`Enter a valid ${parameter.type} value for ${parameter.parameter_id}.`)
    }
  })
}

export type DeclaredType = 'string' | 'boolean' | 'integer' | 'decimal' | 'date' | 'timestamp'
export type WhenOmitted = 'required' | 'default' | 'null'
export type DeclarationDraft = {
  key: string
  name: string
  type: DeclaredType
  multiple: boolean
  omitted: WhenOmitted
  defaultValue: string
  precision: string
  scale: string
}

export const MAX_PARAMETERS = 32
const NAME = /^[A-Za-z][A-Za-z0-9_]{0,63}$/
export const TYPE_LABELS: Record<DeclaredType, string> = {
  string: 'Text', boolean: 'Boolean', integer: 'Integer', decimal: 'Decimal', date: 'Date', timestamp: 'Timestamp',
}

export function newDeclaration(): DeclarationDraft {
  return { key: crypto.randomUUID(), name: '', type: 'string', multiple: false, omitted: 'required',
    defaultValue: '', precision: '18', scale: '2' }
}

function whole(text: string): number | null {
  return /^[0-9]{1,2}$/.test(text) ? Number(text) : null
}

/** Builds wire declarations; throws an Error with a message naming the first invalid parameter. */
export function declarationsFromDrafts(drafts: DeclarationDraft[]): Parameter[] {
  const names = new Set<string>()
  return drafts.map((draft, index) => {
    const label = draft.name.trim() || `parameter ${index + 1}`
    const name = draft.name.trim()
    if (!NAME.test(name)) {
      throw new Error(`Name ${label} with a letter followed by letters, digits or underscores (up to 64).`)
    }
    if (names.has(name.toLowerCase())) throw new Error(`Each parameter needs a unique name; ${name} is repeated.`)
    names.add(name.toLowerCase())
    const precision = whole(draft.precision)
    const scale = whole(draft.scale)
    if (draft.type === 'decimal' && (precision === null || scale === null || precision < 1 || precision > 38 || scale > precision)) {
      throw new Error(`Give ${name} a precision from 1 to 38 and a scale no larger than the precision.`)
    }
    let defaults: Parameter['defaults'] = []
    if (draft.omitted === 'default') {
      try {
        defaults = [parameterScalar(draft.type, draft.defaultValue) as NonNullable<Parameter['defaults']>[number]]
      } catch {
        throw new Error(`Choose a default value for ${name}.`)
      }
    }
    const parsed = parameterSchema.safeParse({
      parameter_id: name, type: draft.type, multiple: draft.multiple,
      required: draft.omitted === 'required', nullable: draft.omitted === 'null', defaults,
      ...(draft.type === 'decimal' ? { precision, scale } : {}),
    })
    if (!parsed.success) throw new Error(`Check the default value for ${name}.`)
    return parsed.data
  })
}
