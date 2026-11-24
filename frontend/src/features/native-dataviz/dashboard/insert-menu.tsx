import { useMemo, useState } from 'react'
import { Circle, Minus, RectangleHorizontal, Shapes, Square, Sticker, Triangle, type LucideIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { ICONS } from './icons'
import * as insert from './insert'

/**
 * A toolbar button's words: shown when the toolbar row has room, otherwise
 * only the icon shows and the words stay for screen readers (and the name).
 */
export function ToolLabel({ children }: { children: React.ReactNode }) {
  return <span className='sr-only @[60rem]/toolbar:not-sr-only'>{children}</span>
}

const SHAPE_ICONS: Record<insert.ShapeKind, LucideIcon> = {
  rect: Square, ellipse: Circle, line: Minus, pill: RectangleHorizontal, triangle: Triangle,
}

/** Insert a shape: rectangle, ellipse, line, pill or triangle. */
export function ShapeMenu({ disabled, onAdd }: { disabled: boolean; onAdd: (shape: insert.ShapeKind) => void }) {
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><Button variant='ghost' size='sm' disabled={disabled}><Shapes /><ToolLabel>Shape</ToolLabel></Button></DropdownMenuTrigger>
    <DropdownMenuContent align='start'>
      {(Object.keys(insert.SHAPE_NAMES) as insert.ShapeKind[]).map(shape => {
        const Icon = SHAPE_ICONS[shape]
        return <DropdownMenuItem key={shape} onSelect={() => onAdd(shape)}><Icon />{insert.SHAPE_NAMES[shape]}</DropdownMenuItem>
      })}
    </DropdownMenuContent>
  </DropdownMenu>
}

export function IconButton({ disabled, onAdd }: { disabled: boolean; onAdd: (icon: insert.IconName) => void }) {
  const [open, setOpen] = useState(false)
  return <>
    <Button variant='ghost' size='sm' disabled={disabled} onClick={() => setOpen(true)}><Sticker /><ToolLabel>Icon</ToolLabel></Button>
    {open && <IconPicker open onOpenChange={setOpen} onPick={onAdd} />}
  </>
}

const NAMES = (Object.keys(ICONS) as insert.IconName[]).map(icon => ({ icon, words: insert.iconWords(icon) }))

/**
 * The icons a dashboard may use, searchable by name. Arrow keys move between
 * them; Enter or a click picks one and closes the dialog.
 */
export function IconPicker({ open, onOpenChange, onPick, current }: {
  open: boolean; onOpenChange: (open: boolean) => void; onPick: (icon: insert.IconName) => void; current?: insert.IconName
}) {
  const [query, setQuery] = useState('')
  const shown = useMemo(() => {
    const words = query.trim().toLowerCase()
    return words ? NAMES.filter(item => item.words.toLowerCase().includes(words) || item.icon.includes(words)) : NAMES
  }, [query])
  const choose = (icon: insert.IconName) => { onPick(icon); onOpenChange(false) }
  // Arrow keys move through the grid by its columns.
  const move = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const steps: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 6, ArrowUp: -6 }
    const step = steps[event.key]
    if (!step) return
    const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button')]
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement)
    const next = buttons[Math.max(0, Math.min(buttons.length - 1, (at < 0 ? 0 : at + step)))]
    if (next) { event.preventDefault(); next.focus() }
  }
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className='sm:max-w-md'>
      <DialogHeader>
        <DialogTitle>{current ? 'Change icon' : 'Add an icon'}</DialogTitle>
        <DialogDescription>Icons come from Nova’s icon set, so they match the rest of the dashboard.</DialogDescription>
      </DialogHeader>
      <Input aria-label='Search icons' placeholder='Search icons' value={query} onChange={event => setQuery(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'ArrowDown') { event.preventDefault(); (event.currentTarget.parentElement?.querySelector('[data-icon-grid] button') as HTMLButtonElement | null)?.focus() }
          if (event.key === 'Enter' && shown.length === 1) { event.preventDefault(); choose(shown[0].icon) }
        }} />
      {shown.length ? <div data-icon-grid role='group' aria-label='Icons' onKeyDown={move}
        className='grid max-h-72 grid-cols-6 gap-1 overflow-y-auto'>
        {shown.map(({ icon, words }) => {
          const Icon = ICONS[icon]
          return <button key={icon} type='button' aria-label={words} title={words} aria-pressed={icon === current}
            onClick={() => choose(icon)}
            className={cn('flex aspect-square items-center justify-center rounded-md border border-transparent text-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
              icon === current && 'border-brand bg-brand/10 text-brand')}>
            <Icon className='size-5' aria-hidden />
          </button>
        })}
      </div> : <p className='type-meta py-6 text-center'>No icon matches “{query.trim()}”. Try a simpler word, such as cart or chart.</p>}
    </DialogContent>
  </Dialog>
}
