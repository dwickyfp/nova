import { useState } from 'react'
import {
  AlignCenterHorizontal, AlignCenterVertical, AlignEndHorizontal, AlignEndVertical, AlignHorizontalSpaceAround,
  AlignStartHorizontal, AlignStartVertical, AlignVerticalSpaceAround, ArrowDownToLine, ArrowUpToLine, ChevronDown,
  Copy, Eye, Grid3x3, Group, LayoutGrid, Lock, Magnet, MoveDown, MoveUp, Settings2, Trash2, Ungroup,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuShortcut, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import type { PageCanvas } from '../contracts'
import * as commands from './commands'
import { frameGrid, pageSize } from './frame'
import type { Page } from './layout'

const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
const MOD = MAC ? '⌘' : 'Ctrl+'
const SHIFT = MAC ? '⇧' : 'Shift+'

/** Preset page sizes; the server holds the same table. */
const PAGE_SIZES: Record<Exclude<NonNullable<PageCanvas['size']>, 'custom'>, { width: number; height: number; label: string }> = {
  '16:9': { width: 1280, height: 720, label: 'Widescreen 16:9' },
  '4:3': { width: 1024, height: 768, label: 'Standard 4:3' },
  'a4-landscape': { width: 1123, height: 794, label: 'A4 landscape' },
  'letter-landscape': { width: 1056, height: 816, label: 'Letter landscape' },
}

/** The page's canvas settings in full, filling what an older page leaves out. */
function canvasOf(page: Page): Required<Omit<PageCanvas, 'background'>> & Pick<PageCanvas, 'background'> {
  const { width, height } = pageSize(page)
  const grid = page.canvas?.grid
  return {
    size: page.canvas?.size ?? 'custom', width, height, fit: page.canvas?.fit ?? 'page', background: page.canvas?.background ?? null,
    grid: { ...frameGrid(page), show: grid?.show ?? true, snap: grid?.snap ?? true, snap_to_elements: grid?.snap_to_elements ?? true },
  }
}

type Props = {
  page: Page
  selection: readonly string[]
  disabled: boolean
  run: (command: commands.Command) => void
}

/** Edit-mode arrangement: the Arrange menu, grid and snap toggles, and page settings. */
export function ArrangeMenu({ page, selection, disabled, run }: Props) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const canvas = canvasOf(page)
  const setGrid = (patch: Partial<typeof canvas.grid>, message: string) =>
    run({ changes: [{ op: 'page.canvas.set', page_id: page.page_id, canvas: { ...canvas, grid: { ...canvas.grid, ...patch } } }], message })
  const items = commands.chosen(page, selection)
  const count = items.length
  const groups = items.some(item => item.element?.kind === 'group')
  const item = (label: string, icon: React.ReactNode, command: () => commands.Command, enabled: boolean, shortcut?: string) =>
    <DropdownMenuItem disabled={!enabled} onSelect={() => run(command())}>
      {icon}{label}{shortcut && <DropdownMenuShortcut>{shortcut}</DropdownMenuShortcut>}
    </DropdownMenuItem>
  return <>
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant='ghost' size='sm' disabled={disabled}>Arrange<ChevronDown className='opacity-60' /></Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align='start' className='w-64'>
        {item('Align left', <AlignStartVertical />, () => commands.alignCommand(page, selection, 'left'), count > 0)}
        {item('Align centre', <AlignCenterVertical />, () => commands.alignCommand(page, selection, 'center'), count > 0)}
        {item('Align right', <AlignEndVertical />, () => commands.alignCommand(page, selection, 'right'), count > 0)}
        {item('Align top', <AlignStartHorizontal />, () => commands.alignCommand(page, selection, 'top'), count > 0)}
        {item('Align middle', <AlignCenterHorizontal />, () => commands.alignCommand(page, selection, 'middle'), count > 0)}
        {item('Align bottom', <AlignEndHorizontal />, () => commands.alignCommand(page, selection, 'bottom'), count > 0)}
        <DropdownMenuSeparator />
        {item('Space evenly across', <AlignHorizontalSpaceAround />, () => commands.distributeCommand(page, selection, 'x'), count > 2)}
        {item('Space evenly down', <AlignVerticalSpaceAround />, () => commands.distributeCommand(page, selection, 'y'), count > 2)}
        {item(count > 1 ? 'Auto-arrange selected' : 'Auto-arrange page', <LayoutGrid />, () => commands.autoArrangeCommand(page, selection), true)}
        <DropdownMenuSeparator />
        {item('Bring to front', <ArrowUpToLine />, () => commands.restackCommand(page, selection, 'front'), count > 0, `${SHIFT}${MOD}]`)}
        {item('Bring forward', <MoveUp />, () => commands.restackCommand(page, selection, 'forward'), count > 0, `${MOD}]`)}
        {item('Send backward', <MoveDown />, () => commands.restackCommand(page, selection, 'backward'), count > 0, `${MOD}[`)}
        {item('Send to back', <ArrowDownToLine />, () => commands.restackCommand(page, selection, 'back'), count > 0, `${SHIFT}${MOD}[`)}
        <DropdownMenuSeparator />
        {item('Group', <Group />, () => commands.groupCommand(page, selection, crypto.randomUUID()), count > 1, `${MOD}G`)}
        {item('Ungroup', <Ungroup />, () => commands.ungroupCommand(page, selection), groups, `${SHIFT}${MOD}G`)}
        {item(count && items.every(entry => entry.locked) ? 'Unlock' : 'Lock', <Lock />, () => commands.toggleCommand(page, selection, 'locked'), count > 0, `${SHIFT}${MOD}L`)}
        {item(count && items.every(entry => entry.element?.hidden) ? 'Show' : 'Hide', <Eye />, () => commands.toggleCommand(page, selection, 'hidden'), count > 0, `${SHIFT}${MOD}H`)}
        {item('Duplicate', <Copy />, () => commands.duplicateCommand(page, selection, () => crypto.randomUUID()), count > 0, `${MOD}D`)}
        {item('Delete', <Trash2 />, () => commands.removeCommand(page, selection), count > 0, 'Del')}
      </DropdownMenuContent>
    </DropdownMenu>
    <Tooltip><TooltipTrigger asChild>
      <Button variant='ghost' size='icon' className={cn('size-8', canvas.grid.show && 'bg-accent text-accent-foreground')} aria-label='Show grid'
        aria-pressed={canvas.grid.show} disabled={disabled}
        onClick={() => setGrid({ show: !canvas.grid.show }, canvas.grid.show ? 'Grid hidden' : 'Grid shown')}><Grid3x3 /></Button>
    </TooltipTrigger><TooltipContent>Show grid</TooltipContent></Tooltip>
    <Tooltip><TooltipTrigger asChild>
      <Button variant='ghost' size='icon' className={cn('size-8', canvas.grid.snap && 'bg-accent text-accent-foreground')} aria-label='Snap to grid'
        aria-pressed={canvas.grid.snap} disabled={disabled}
        onClick={() => setGrid({ snap: !canvas.grid.snap }, canvas.grid.snap ? 'Snapping off' : 'Snapping on')}><Magnet /></Button>
    </TooltipTrigger><TooltipContent>Snap to grid (hold Alt or {MAC ? '⌘' : 'Ctrl'} to place freely)</TooltipContent></Tooltip>
    <Tooltip><TooltipTrigger asChild>
      <Button variant='ghost' size='icon' className='size-8' aria-label='Page and grid settings' disabled={disabled} onClick={() => setSettingsOpen(true)}><Settings2 /></Button>
    </TooltipTrigger><TooltipContent>Page and grid settings</TooltipContent></Tooltip>
    {settingsOpen && <PageSettings page={page} open onOpenChange={setSettingsOpen} run={run} />}
  </>
}

type Draft = { size: NonNullable<PageCanvas['size']>; width: string; height: string; fit: NonNullable<PageCanvas['fit']>;
  columns: string; gutter: string; margin: string; baseline: string; snapToElements: boolean }

const LIMITS = {
  width: [320, 4096], height: [200, 750_000], columns: [1, 24], gutter: [0, 64], margin: [0, 256], baseline: [2, 64],
} as const

function PageSettings({ page, open, onOpenChange, run }: { page: Page; open: boolean; onOpenChange: (open: boolean) => void; run: Props['run'] }) {
  const canvas = canvasOf(page)
  const [draft, setDraft] = useState<Draft>({
    size: canvas.size, width: String(canvas.width), height: String(canvas.height), fit: canvas.fit,
    columns: String(canvas.grid.columns), gutter: String(canvas.grid.gutter), margin: String(canvas.grid.margin),
    baseline: String(canvas.grid.baseline), snapToElements: canvas.grid.snap_to_elements ?? true,
  })
  const set = (patch: Partial<Draft>) => setDraft(current => ({ ...current, ...patch }))
  const number = (key: keyof typeof LIMITS) => {
    const value = Number(draft[key])
    const [low, high] = LIMITS[key]
    return Number.isInteger(value) && value >= low && value <= high ? value : null
  }
  const values = { width: number('width'), height: number('height'), columns: number('columns'), gutter: number('gutter'),
    margin: number('margin'), baseline: number('baseline') }
  const invalid = (Object.keys(values) as (keyof typeof values)[]).filter(key => values[key] === null)
  const fits = !invalid.length && values.width! - 2 * values.margin! - (values.columns! - 1) * values.gutter! >= 8 * values.columns!
  const error = invalid.length
    ? `Enter a whole number for ${invalid.map(key => `${key} (${LIMITS[key][0]}–${LIMITS[key][1].toLocaleString()})`).join(', ')}.`
    : fits ? null : 'The columns do not fit the page width. Use fewer columns or a smaller gutter or margin.'
  const save = () => {
    if (error) return
    const next: PageCanvas = { ...canvas, size: draft.size, width: values.width!, height: values.height!, fit: draft.fit,
      grid: { ...canvas.grid, columns: values.columns!, gutter: values.gutter!, margin: values.margin!, baseline: values.baseline!, snap_to_elements: draft.snapToElements } }
    run({ changes: [{ op: 'page.canvas.set', page_id: page.page_id, canvas: next }], message: 'Page settings saved' })
    onOpenChange(false)
  }
  const field = (key: keyof typeof LIMITS, label: string, disabled = false) => <div className='grid gap-1.5'>
    <Label htmlFor={`page-${key}`}>{label}</Label>
    <Input id={`page-${key}`} inputMode='numeric' value={draft[key]} disabled={disabled} aria-invalid={values[key] === null}
      onChange={event => set({ [key]: event.target.value } as Partial<Draft>)} />
  </div>
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className='sm:max-w-md'>
      <DialogHeader>
        <DialogTitle>Page and grid settings</DialogTitle>
        <DialogDescription>The grid guides placement while you edit. Viewers do not see it.</DialogDescription>
      </DialogHeader>
      <form className='grid gap-4' onSubmit={event => { event.preventDefault(); save() }}>
        <div className='grid gap-1.5'>
          <Label htmlFor='page-size'>Page size</Label>
          <Select value={draft.size} onValueChange={value => {
            const size = value as Draft['size']
            const preset = size === 'custom' ? null : PAGE_SIZES[size]
            set(preset ? { size, width: String(preset.width), height: String(preset.height) } : { size })
          }}>
            <SelectTrigger id='page-size'><SelectValue /></SelectTrigger>
            <SelectContent>
              {Object.entries(PAGE_SIZES).map(([key, preset]) => <SelectItem key={key} value={key}>{preset.label} ({preset.width} × {preset.height})</SelectItem>)}
              <SelectItem value='custom'>Custom</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className='grid grid-cols-2 gap-3'>
          {field('width', 'Width (px)', draft.size !== 'custom')}
          {field('height', 'Height (px)', draft.size !== 'custom')}
        </div>
        <div className='grid gap-1.5'>
          <Label htmlFor='page-fit'>Show the page</Label>
          <Select value={draft.fit} onValueChange={value => set({ fit: value as Draft['fit'] })}>
            <SelectTrigger id='page-fit'><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value='page'>Whole page</SelectItem>
              <SelectItem value='width'>Fit to width</SelectItem>
              <SelectItem value='actual'>Actual size</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <fieldset className='grid grid-cols-2 gap-3'>
          <legend className='mb-2 text-sm font-medium'>Grid</legend>
          {field('columns', 'Columns')}
          {field('gutter', 'Gutter (px)')}
          {field('margin', 'Margin (px)')}
          {field('baseline', 'Row height (px)')}
        </fieldset>
        <div className='flex items-center justify-between gap-3'>
          <Label htmlFor='page-snap-elements'>Snap to other items’ edges and centres</Label>
          <Switch id='page-snap-elements' checked={draft.snapToElements} onCheckedChange={checked => set({ snapToElements: checked === true })} />
        </div>
        {error && <p role='alert' className='text-sm text-destructive'>{error}</p>}
        <DialogFooter>
          <Button type='button' variant='outline' onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type='submit' disabled={Boolean(error)}>Save</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}
