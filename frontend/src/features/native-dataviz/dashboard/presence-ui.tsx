import { MousePointer2 } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { colourFor, initials, ROW_PX, type Collaborator } from './presence'

const SHOWN = 4

/** Everyone else on this dashboard, one avatar per person. */
export function PresenceAvatars({ others, more }: { others: Collaborator[]; more: number }) {
  // Two tabs of one person are one avatar; editing in either counts.
  const people = [...others.reduce((all, item) => {
    const known = all.get(item.name)
    all.set(item.name, known ? { ...known, editing: known.editing || item.editing } : item)
    return all
  }, new Map<string, Collaborator>()).values()]
  if (!people.length) return null
  const hidden = people.length - SHOWN + more
  const names = people.map(person => `${person.name}${person.editing ? ' (editing)' : ''}`).join(', ')
  return <div role='group' aria-label={`Also here: ${names}`} className='flex shrink-0 items-center -space-x-1.5 @max-md/editor:hidden'>
    {people.slice(0, SHOWN).map(person => <Tooltip key={person.name}>
      <TooltipTrigger asChild>
        <span tabIndex={0} className='relative flex size-7 items-center justify-center rounded-full border-2 border-background bg-muted text-[11px] font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring'
          style={{ boxShadow: `inset 0 0 0 1.5px ${colourFor(person.viewer)}` }}>
          {initials(person.name)}
          {person.editing && <span aria-hidden className='absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full border-2 border-background'
            style={{ background: colourFor(person.viewer) }} />}
        </span>
      </TooltipTrigger>
      <TooltipContent>{person.name} · {person.editing ? 'editing' : 'viewing'}</TooltipContent>
    </Tooltip>)}
    {hidden > 0 && <span className='flex size-7 items-center justify-center rounded-full border-2 border-background bg-muted text-[11px] text-muted-foreground'>
      +{hidden}
    </span>}
  </div>
}

/** Other people's pointers on this page, gliding between heartbeats. */
export function PresenceCursors({ others, pageId }: { others: Collaborator[]; pageId: string }) {
  const here = others.filter(item => item.page_id === pageId && item.cursor)
  return <div aria-hidden className='pointer-events-none absolute inset-0 z-20 overflow-hidden'>
    {here.map(item => {
      const colour = colourFor(item.viewer)
      return <div key={item.viewer}
        className='absolute flex items-start motion-safe:transition-[left,top] motion-safe:duration-1000 motion-safe:ease-linear'
        style={{ left: `${item.cursor!.x * 100}%`, top: item.cursor!.y * ROW_PX }}>
        <MousePointer2 className='size-4 drop-shadow-sm' style={{ color: colour }} fill='currentColor' strokeWidth={1.5} stroke='var(--background)' />
        <span className='mt-3.5 -ml-1 flex max-w-32 items-center gap-1 rounded bg-foreground px-1.5 py-0.5 text-[11px] leading-4 font-medium text-background shadow-sm'>
          <span className='size-1.5 shrink-0 rounded-full' style={{ background: colour }} />
          <span className='truncate'>{item.name}</span>
        </span>
      </div>
    })}
  </div>
}
