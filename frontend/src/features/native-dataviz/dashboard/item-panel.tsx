import { useEffect, useState } from 'react'
import { ChevronDown, ChevronUp, Eye, EyeOff, Lock, LockOpen, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import type { ConnectorElement, ElementStyle, ShapeElement, Stroke, TextElement } from '../contracts'
import { index } from './authoring'
import * as commands from './commands'
import { tabOrder } from './frame'
import { ICONS } from './icons'
import { IconPicker } from './insert-menu'
import * as insert from './insert'
import type { Element, Page } from './layout'

type Run = (command: commands.Command) => void
const NONE = 'none'
type Props = {
  page: Page
  selection: string[]
  onSelect: (selection: string[]) => void
  run: Run
  disabled: boolean
  onClose: () => void
}

function Swatch({ token }: { token: insert.Token }) {
  return <span aria-hidden className='size-3.5 shrink-0 rounded-sm border border-border' style={{ background: `var(--${token})` }} />
}

/** A theme colour, or none. */
function TokenSelect({ id, label, value, onChange, allowNone = true, disabled }: {
  id: string; label?: string; value: insert.Token | undefined; onChange: (token: insert.Token | null) => void; allowNone?: boolean; disabled?: boolean
}) {
  return <Select value={value ?? NONE} onValueChange={next => onChange(next === NONE ? null : next as insert.Token)} disabled={disabled}>
    <SelectTrigger id={id} aria-label={label} className='h-8 w-full'><SelectValue /></SelectTrigger>
    <SelectContent>
      {allowNone && <SelectItem value={NONE}>None</SelectItem>}
      {insert.TOKENS.map(item => <SelectItem key={item.token} value={item.token}>
        <span className='flex items-center gap-2'><Swatch token={item.token} />{item.label}</span>
      </SelectItem>)}
    </SelectContent>
  </Select>
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return <div className='grid gap-1.5'><Label htmlFor={id} className='type-meta text-foreground'>{label}</Label>{children}</div>
}

/** A text field that saves when you leave it or press Enter, and goes back on Escape. */
function CommitInput({ id, value, onCommit, maxLength, disabled, placeholder }: {
  id: string; value: string; onCommit: (value: string) => void; maxLength: number; disabled?: boolean; placeholder?: string
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  const commit = () => { if (draft.trim() !== value) onCommit(draft.trim()) }
  return <Input id={id} className='h-8' value={draft} maxLength={maxLength} disabled={disabled} placeholder={placeholder}
    onChange={event => setDraft(event.target.value)} onBlur={commit}
    onKeyDown={event => {
      if (event.key === 'Enter') { event.preventDefault(); commit() }
      if (event.key === 'Escape') { event.stopPropagation(); setDraft(value) }
    }} />
}

export function ItemPanel({ page, selection, onSelect, run, disabled, onClose }: Props) {
  const [tab, setTab] = useState('format')
  return <aside aria-label='Format and layers' className='flex min-h-0 w-full flex-col'>
    <header className='flex h-12 shrink-0 items-center justify-between gap-2 border-b pr-2 pl-3'>
      <h2 className='truncate text-sm font-medium'>Format and layers</h2>
      <Button variant='ghost' size='icon' className='size-8 text-muted-foreground hover:text-foreground' aria-label='Close format and layers' onClick={onClose}><X /></Button>
    </header>
    <Tabs value={tab} onValueChange={setTab} className='flex min-h-0 flex-1 flex-col gap-0'>
      <TabsList className='mx-3 mt-2 h-8'>
        <TabsTrigger value='format'>Format</TabsTrigger>
        <TabsTrigger value='layers'>Layers</TabsTrigger>
        <TabsTrigger value='tab-order'>Tab order</TabsTrigger>
      </TabsList>
      <TabsContent value='format' className='min-h-0 flex-1 overflow-y-auto px-3 py-3'>
        <FormatTab page={page} selection={selection} run={run} disabled={disabled} />
      </TabsContent>
      <TabsContent value='layers' className='min-h-0 flex-1 overflow-y-auto py-2'>
        <LayersTab page={page} selection={selection} onSelect={onSelect} run={run} disabled={disabled} />
      </TabsContent>
      <TabsContent value='tab-order' className='min-h-0 flex-1 overflow-y-auto py-2'>
        <TabOrderTab page={page} selection={selection} onSelect={onSelect} run={run} disabled={disabled} />
      </TabsContent>
    </Tabs>
  </aside>
}

// ── Format ──────────────────────────────────────────────────────────────

type TextRole = NonNullable<TextElement['role']>
const TEXT_ROLES: Record<TextRole, string> = { title: 'Title', heading: 'Heading', body: 'Body', caption: 'Caption' }

/** Items that draw their content inside a frame, where padding applies. */
const PADDED = new Set(['text', 'visual', 'filter'])
const PADDINGS = [0, 4, 8, 12, 16, 24] as const
const RADII: { value: NonNullable<ElementStyle['radius']>; label: string }[] = [
  { value: 0, label: 'Square' }, { value: 4, label: 'Small' }, { value: 8, label: 'Medium' }, { value: 12, label: 'Large' },
  { value: 16, label: 'Extra large' }, { value: 9999, label: 'Round' },
]

function FormatTab({ page, selection, run, disabled }: { page: Page; selection: string[]; run: Run; disabled: boolean }) {
  const found = index(page)
  const items = selection.flatMap(id => found.get(id)?.element ?? [])
  if (!items.length) return <PageFormat page={page} run={run} disabled={disabled} />
  const one = items.length === 1 ? items[0] : null
  const style: ElementStyle = one?.style ?? {}
  const fill = style.fill
  const solid = fill && fill.kind === 'solid' ? fill.token : undefined
  const stroke = style.stroke ?? undefined
  const setStyle = (change: Partial<ElementStyle>, message: string) => run(insert.styleCommand(page, selection, change, message))
  const setStroke = (next: Partial<Stroke> | null) => setStyle({ stroke: next === null ? null
    : { token: stroke?.token ?? 'border', width: stroke?.width ?? 1, dash: stroke?.dash ?? 'solid', ...next } }, next === null ? 'Outline removed' : 'Outline changed')
  const allDecorative = items.every(item => item.decorative)
  return <div className='grid gap-4'>
    <p className='type-meta'>{one ? `${insert.kindName(one) ?? 'Item'} selected` : `${items.length} items selected; changes apply to each`}</p>
    {one && <Field id='format-name' label='Name in the layers list'>
      <CommitInput id='format-name' value={one.name ?? ''} maxLength={120} disabled={disabled} placeholder={insert.itemName(one)}
        onCommit={name => run(insert.propertyCommand(page, one.element_id, { name }, name ? `Renamed to ${name}` : 'Name cleared'))} />
    </Field>}
    {one?.kind === 'shape' && <ShapeFields page={page} shape={one as ShapeElement} run={run} disabled={disabled} />}
    {one?.kind === 'text' && <Field id='format-role' label='Text style'>
      <Select value={one.role ?? 'body'} disabled={disabled}
        onValueChange={value => run(insert.propertyCommand(page, one.element_id, { role: value as TextRole }, `Text style set to ${TEXT_ROLES[value as TextRole].toLowerCase()}`))}>
        <SelectTrigger id='format-role' className='h-8 w-full'><SelectValue /></SelectTrigger>
        <SelectContent>{Object.entries(TEXT_ROLES).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent>
      </Select>
    </Field>}
    {one?.kind === 'icon' && <IconFields page={page} element={one} run={run} disabled={disabled} />}
    {one?.kind === 'connector' && <ConnectorFields page={page} element={one as ConnectorElement} run={run} disabled={disabled} />}
    {one?.kind !== 'connector' && one?.kind !== 'icon' && <Field id='format-fill' label='Fill'>
      <TokenSelect id='format-fill' value={solid} disabled={disabled}
        onChange={token => setStyle({ fill: token ? { kind: 'solid', token, opacity: 100 } : null }, token ? 'Fill changed' : 'Fill removed')} />
    </Field>}
    <fieldset className='grid gap-2'>
      <legend className='type-meta mb-1.5 text-foreground'>{one?.kind === 'icon' ? 'Colour and line' : one?.kind === 'connector' ? 'Line' : 'Outline'}</legend>
      <TokenSelect id='format-outline' label={one?.kind === 'icon' ? 'Icon colour' : one?.kind === 'connector' ? 'Line colour' : 'Outline colour'} value={stroke?.token} disabled={disabled} allowNone={one?.kind !== 'connector' && one?.kind !== 'icon'}
        onChange={token => setStroke(token ? { token } : null)} />
      <div className='grid grid-cols-2 gap-2'>
        <Select value={String(stroke?.width ?? 1)} onValueChange={value => setStroke({ width: Number(value) as Stroke['width'] })} disabled={disabled || !stroke}>
          <SelectTrigger aria-label='Line width' className='h-8 w-full'><SelectValue /></SelectTrigger>
          <SelectContent>{[1, 2, 3, 4].map(width => <SelectItem key={width} value={String(width)}>{width} px</SelectItem>)}</SelectContent>
        </Select>
        <Select value={stroke?.dash ?? 'solid'} onValueChange={value => setStroke({ dash: value as Stroke['dash'] })} disabled={disabled || !stroke}>
          <SelectTrigger aria-label='Line style' className='h-8 w-full'><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value='solid'>Solid</SelectItem><SelectItem value='dash'>Dashed</SelectItem><SelectItem value='dot'>Dotted</SelectItem>
          </SelectContent>
        </Select>
      </div>
    </fieldset>
    {one?.kind !== 'connector' && one?.kind !== 'icon' && <div className='grid grid-cols-2 gap-2'>
      <Field id='format-radius' label='Corners'>
        <Select value={String(style.radius ?? 0)} onValueChange={value => setStyle({ radius: Number(value) as ElementStyle['radius'] }, 'Corners changed')} disabled={disabled}>
          <SelectTrigger id='format-radius' className='h-8 w-full'><SelectValue /></SelectTrigger>
          <SelectContent>{RADII.map(item => <SelectItem key={item.value} value={String(item.value)}>{item.label}</SelectItem>)}</SelectContent>
        </Select>
      </Field>
      <Field id='format-shadow' label='Shadow'>
        <Select value={style.shadow ?? 'none'} onValueChange={value => setStyle({ shadow: value as ElementStyle['shadow'] }, 'Shadow changed')} disabled={disabled}>
          <SelectTrigger id='format-shadow' className='h-8 w-full'><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value='none'>None</SelectItem><SelectItem value='sm'>Soft</SelectItem><SelectItem value='md'>Raised</SelectItem></SelectContent>
        </Select>
      </Field>
    </div>}
    {one && PADDED.has(one.kind ?? '') && <Field id='format-padding' label='Padding'>
      <Select value={String(style.padding ?? 0)} disabled={disabled}
        onValueChange={value => {
          const padding = Number(value) as NonNullable<ElementStyle['padding']>
          setStyle({ padding }, padding ? `Padding set to ${padding} px` : 'Padding removed')
        }}>
        <SelectTrigger id='format-padding' className='h-8 w-full'><SelectValue /></SelectTrigger>
        <SelectContent>{PADDINGS.map(value => <SelectItem key={value} value={String(value)}>{value ? `${value} px` : 'None'}</SelectItem>)}</SelectContent>
      </Select>
    </Field>}
    <Field id='format-opacity' label='Opacity (%)'>
      <CommitInput id='format-opacity' value={String(style.opacity ?? 100)} maxLength={3} disabled={disabled}
        onCommit={value => {
          const opacity = Number(value)
          if (!/^\d+$/.test(value) || opacity < 0 || opacity > 100) run({ changes: null, message: 'Enter a whole number from 0 to 100 for opacity' })
          else setStyle({ opacity }, `Opacity set to ${opacity}%`)
        }} />
    </Field>
    <div className='flex items-start justify-between gap-3'>
      <div className='grid gap-0.5'>
        <Label htmlFor='format-decorative' className='type-ui'>Decorative</Label>
        <p className='type-meta'>Screen readers and the keyboard skip decorative items.</p>
      </div>
      <Switch id='format-decorative' checked={allDecorative} disabled={disabled}
        onCheckedChange={checked => run(insert.decorativeCommand(page, selection, checked))} />
    </div>
  </div>
}

function ShapeFields({ page, shape, run, disabled }: { page: Page; shape: ShapeElement; run: Run; disabled: boolean }) {
  return <Field id='format-shape' label='Shape'>
    <Select value={shape.shape ?? 'rect'} disabled={disabled}
      onValueChange={value => run(insert.propertyCommand(page, shape.element_id, { shape: value as insert.ShapeKind },
        `Changed to a ${insert.SHAPE_NAMES[value as insert.ShapeKind].toLowerCase()}`))}>
      <SelectTrigger id='format-shape' className='h-8 w-full'><SelectValue /></SelectTrigger>
      <SelectContent>{Object.entries(insert.SHAPE_NAMES).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent>
    </Select>
  </Field>
}

function IconFields({ page, element, run, disabled }: { page: Page; element: Element; run: Run; disabled: boolean }) {
  const [picking, setPicking] = useState(false)
  if (element.kind !== 'icon') return null
  const Icon = ICONS[element.icon]
  return <>
    <Field id='format-icon' label='Icon'>
      <Button id='format-icon' variant='outline' size='sm' className='justify-start' disabled={disabled} onClick={() => setPicking(true)}>
        <Icon aria-hidden />{insert.iconWords(element.icon)}<span className='ml-auto type-meta'>Change</span>
      </Button>
    </Field>
    <Field id='format-label' label='Label for screen readers'>
      <CommitInput id='format-label' value={element.label ?? ''} maxLength={256} disabled={disabled} placeholder='Leave empty if decorative'
        onCommit={label => run(label && element.decorative
          ? withLabel(page, element.element_id, label)
          : insert.propertyCommand(page, element.element_id, { label }, label ? `Label set to ${label}` : 'Label cleared'))} />
    </Field>
    {picking && <IconPicker open onOpenChange={setPicking} current={element.icon}
      onPick={icon => run(insert.propertyCommand(page, element.element_id, { icon, ...(element.name === insert.iconWords(element.icon) ? { name: insert.iconWords(icon) } : {}) },
        `Changed to the ${insert.iconWords(icon).toLowerCase()} icon`))} />}
  </>
}

/** Giving a decorative icon a label also makes it read: one change with both. */
function withLabel(page: Page, id: string, label: string): commands.Command {
  return { changes: [{ op: 'elements.update', page_id: page.page_id, updates: [{ element_id: id, set: { label, decorative: false } }] }],
    message: `Label set to ${label}; screen readers now read this icon` }
}

function ConnectorFields({ page, element, run, disabled }: { page: Page; element: ConnectorElement; run: Run; disabled: boolean }) {
  return <div className='grid grid-cols-2 gap-2'>
    <Field id='format-head' label='Ending'>
      <Select value={element.head ?? 'arrow'} disabled={disabled}
        onValueChange={value => run(insert.propertyCommand(page, element.element_id, { head: value as ConnectorElement['head'] }, 'Line ending changed'))}>
        <SelectTrigger id='format-head' className='h-8 w-full'><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value='arrow'>Arrow</SelectItem><SelectItem value='dot'>Dot</SelectItem><SelectItem value='none'>None</SelectItem></SelectContent>
      </Select>
    </Field>
    <Field id='format-curve' label='Path'>
      <Select value={element.curve ?? 'straight'} disabled={disabled}
        onValueChange={value => run(insert.propertyCommand(page, element.element_id, { curve: value as ConnectorElement['curve'] }, 'Line path changed'))}>
        <SelectTrigger id='format-curve' className='h-8 w-full'><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value='straight'>Straight</SelectItem><SelectItem value='elbow'>Elbow</SelectItem><SelectItem value='arc'>Curved</SelectItem></SelectContent>
      </Select>
    </Field>
  </div>
}

/** With nothing selected, Format sets the page background. */
function PageFormat({ page, run, disabled }: { page: Page; run: Run; disabled: boolean }) {
  const background = page.canvas?.background
  const mode = !background ? NONE : background.kind
  const gradient = background?.kind === 'gradient' ? background : null
  const solid = background?.kind === 'solid' ? background : null
  return <div className='grid gap-4'>
    <p className='type-meta'>Nothing selected. Select an item to format it, or set the page background.</p>
    <Field id='page-background' label='Page background'>
      <Select value={mode} disabled={disabled} onValueChange={value => {
        if (value === NONE) run(insert.backgroundCommand(page, null))
        else if (value === 'solid') run(insert.backgroundCommand(page, { kind: 'solid', token: gradient?.start ?? 'muted', opacity: 100 }))
        else if (value === 'gradient') run(insert.backgroundCommand(page, { kind: 'gradient', start: solid?.token ?? 'background', end: 'muted', angle: 180 }))
      }}>
        <SelectTrigger id='page-background' className='h-8 w-full'><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value={NONE}>None</SelectItem><SelectItem value='solid'>Colour</SelectItem><SelectItem value='gradient'>Gradient</SelectItem>
          {background?.kind === 'image' && <SelectItem value='image' disabled>Image</SelectItem>}
        </SelectContent>
      </Select>
    </Field>
    {solid && <Field id='page-colour' label='Colour'>
      <TokenSelect id='page-colour' value={solid.token} allowNone={false} disabled={disabled}
        onChange={token => token && run(insert.backgroundCommand(page, { ...solid, token }))} />
    </Field>}
    {gradient && <>
      <div className='grid grid-cols-2 gap-2'>
        <Field id='page-from' label='From'>
          <TokenSelect id='page-from' value={gradient.start} allowNone={false} disabled={disabled}
            onChange={token => token && run(insert.backgroundCommand(page, { ...gradient, start: token }))} />
        </Field>
        <Field id='page-to' label='To'>
          <TokenSelect id='page-to' value={gradient.end} allowNone={false} disabled={disabled}
            onChange={token => token && run(insert.backgroundCommand(page, { ...gradient, end: token }))} />
        </Field>
      </div>
      <Field id='page-angle' label='Direction'>
        <Select value={String(gradient.angle ?? 180)} disabled={disabled}
          onValueChange={value => run(insert.backgroundCommand(page, { ...gradient, angle: Number(value) }))}>
          <SelectTrigger id='page-angle' className='h-8 w-full'><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value='180'>Top to bottom</SelectItem><SelectItem value='90'>Left to right</SelectItem>
            <SelectItem value='135'>Diagonal, down</SelectItem><SelectItem value='45'>Diagonal, up</SelectItem>
          </SelectContent>
        </Select>
      </Field>
    </>}
  </div>
}

// ── Layers and tab order ────────────────────────────────────────────────

type Row = { element: Element; depth: number; top: boolean }

/** Front to back, with each group's children under it (also front first). */
function layerRows(elements: readonly Element[], depth = 0): Row[] {
  return [...elements].reverse().flatMap(element => [
    { element, depth, top: depth === 0 },
    ...(element.kind === 'group' ? layerRows(element.children, depth + 1) : []),
  ])
}

function pick(event: React.MouseEvent | React.KeyboardEvent, id: string, selection: string[], onSelect: Props['onSelect']) {
  if (event.shiftKey || event.metaKey || event.ctrlKey) onSelect(selection.includes(id) ? selection.filter(item => item !== id) : [...selection, id])
  else onSelect([id])
}

/** Shift (or Ctrl/Cmd) with Enter or Space adds a row to the selection from the keyboard. */
function pickKey(event: React.KeyboardEvent, id: string, selection: string[], onSelect: Props['onSelect']): boolean {
  if ((event.key !== 'Enter' && event.key !== ' ') || !(event.shiftKey || event.metaKey || event.ctrlKey)) return false
  event.preventDefault()
  event.stopPropagation()
  pick(event, id, selection, onSelect)
  return true
}

function LayersTab({ page, selection, onSelect, run, disabled }: Omit<Props, 'onClose'>) {
  const rows = layerRows(page.elements ?? [])
  const top = (page.elements ?? []).length
  if (!rows.length) return <p className='type-meta px-3'>Nothing on this page yet.</p>
  return <>
    <p className='type-meta px-3 pb-2'>Front to back. Items higher in the list cover the ones below them.</p>
    <ol aria-label='Layers, front to back' className='grid'>
      {rows.map(({ element, depth, top: onPage }) => {
        const id = element.element_id
        const selected = selection.includes(id)
        const position = onPage ? top - 1 - (page.elements ?? []).findIndex(item => item.element_id === id) : -1
        const name = insert.itemName(element)
        return <li key={id} className={cn('group/row flex items-center gap-1 pr-2', selected ? 'bg-brand/10' : 'hover:bg-muted/60')}
          style={{ paddingLeft: 12 + depth * 16 }}>
          <button type='button' aria-pressed={selected} onClick={event => pick(event, id, selection, onSelect)}
            onKeyDown={event => {
              if (pickKey(event, id, selection, onSelect)) return
              if (!onPage || !event.altKey || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return
              // Handled here: the editor's Alt+arrow (move the selection) must not also run.
              event.preventDefault()
              event.stopPropagation()
              run(insert.layerCommand(page, id, position + (event.key === 'ArrowUp' ? -1 : 1)))
            }}
            className={cn('type-ui min-w-0 flex-1 truncate rounded-sm py-1.5 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
              selected && 'text-brand', element.hidden && 'text-muted-foreground')}>
            <span className='type-meta mr-1'>{insert.kindName(element)} </span>{name}
          </button>
          <Button variant='ghost' size='icon' className='size-7' disabled={disabled}
            aria-label={`${element.hidden ? 'Show' : 'Hide'} ${name}`} onClick={() => run(commands.toggleCommand(page, [id], 'hidden'))}>
            {element.hidden ? <EyeOff /> : <Eye />}
          </Button>
          <Button variant='ghost' size='icon' className='size-7' disabled={disabled}
            aria-label={`${element.locked ? 'Unlock' : 'Lock'} ${name}`} onClick={() => run(commands.toggleCommand(page, [id], 'locked'))}>
            {element.locked ? <Lock /> : <LockOpen className='opacity-50' />}
          </Button>
          {onPage && <>
            <Button variant='ghost' size='icon' className='size-7' disabled={disabled || position === 0}
              aria-label={`Bring ${name} forward`} onClick={() => run(insert.layerCommand(page, id, position - 1))}><ChevronUp /></Button>
            <Button variant='ghost' size='icon' className='size-7' disabled={disabled || position === top - 1}
              aria-label={`Send ${name} backward`} onClick={() => run(insert.layerCommand(page, id, position + 1))}><ChevronDown /></Button>
          </>}
        </li>
      })}
    </ol>
    <p className='type-meta px-3 pt-2'>Shift+Enter adds a name to the selection. Alt+Up or Alt+Down moves it. Items inside a group keep the group’s order.</p>
  </>
}

function TabOrderTab({ page, selection, onSelect, run, disabled }: Omit<Props, 'onClose'>) {
  const order = tabOrder(page, { includeHidden: true })
  const custom = (page.tab_order ?? []).length > 0
  if (!order.length) return <p className='type-meta px-3'>Nothing on this page can be reached with the keyboard yet.</p>
  return <>
    <div className='flex items-start justify-between gap-2 px-3 pb-2'>
      <p className='type-meta'>{custom ? 'The order you set. Keyboard and screen-reader users reach items in this order.'
        : 'Follows the page: top to bottom, then left to right. Move an item to set your own order.'}</p>
      {custom && <Button variant='outline' size='sm' className='h-7 shrink-0' disabled={disabled} onClick={() => run(insert.resetTabOrderCommand(page))}>Follow the page</Button>}
    </div>
    <ol aria-label='Tab order' className='grid'>
      {order.map((element, position) => {
        const id = element.element_id
        const selected = selection.includes(id)
        const name = insert.itemName(element)
        return <li key={id} className={cn('flex items-center gap-1 pr-2 pl-3', selected ? 'bg-brand/10' : 'hover:bg-muted/60')}>
          <span className='type-meta w-6 shrink-0 tabular-nums'>{position + 1}</span>
          <button type='button' aria-pressed={selected} onClick={event => pick(event, id, selection, onSelect)}
            onKeyDown={event => {
              if (pickKey(event, id, selection, onSelect)) return
              if (!event.altKey || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return
              // Handled here: the editor's Alt+arrow (move the selection) must not also run.
              event.preventDefault()
              event.stopPropagation()
              run(insert.tabOrderCommand(page, id, position + (event.key === 'ArrowUp' ? -1 : 1)))
            }}
            className={cn('type-ui min-w-0 flex-1 truncate rounded-sm py-1.5 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
              selected && 'text-brand', element.hidden && 'text-muted-foreground')}>
            {name}{element.hidden && <span className='type-meta'> (hidden)</span>}
          </button>
          <Button variant='ghost' size='icon' className='size-7' disabled={disabled || position === 0}
            aria-label={`Move ${name} earlier`} onClick={() => run(insert.tabOrderCommand(page, id, position - 1))}><ChevronUp /></Button>
          <Button variant='ghost' size='icon' className='size-7' disabled={disabled || position === order.length - 1}
            aria-label={`Move ${name} later`} onClick={() => run(insert.tabOrderCommand(page, id, position + 1))}><ChevronDown /></Button>
        </li>
      })}
    </ol>
    <p className='type-meta px-3 pt-2'>Decorative items are left out. Alt+Up or Alt+Down on a name moves it.</p>
  </>
}
