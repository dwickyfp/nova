import { Bot, PanelRightClose } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export type AssistantToggleProps = {
  open: boolean
  onToggle: () => void
}

/**
 * The always-available way to show or hide the assistant. Closed, it floats at
 * the bottom-right of the viewport; open, it moves to the top-right, level with
 * the panel header, so it never covers the composer at the panel's foot. The
 * icon swaps between `PanelRightClose` (open) and `Bot` (closed). It doubles as
 * the trigger for the narrow `Sheet`, so a small viewport never loses it.
 */
export function AssistantToggle({ open, onToggle }: AssistantToggleProps) {
  return (
    <Button
      type='button'
      size='icon'
      variant={open ? 'secondary' : 'default'}
      className={cn(
        'fixed right-4 z-50 min-h-11 min-w-11 rounded-full shadow-lg',
        open ? 'top-4' : 'bottom-4',
        'transition-transform duration-200 motion-reduce:transition-none'
      )}
      aria-label={open ? 'Hide assistant' : 'Show assistant'}
      aria-pressed={open}
      aria-expanded={open}
      aria-controls='assistant-panel'
      onClick={onToggle}
    >
      {open ? (
        <PanelRightClose aria-hidden='true' className='size-5' />
      ) : (
        <Bot aria-hidden='true' className='size-5' />
      )}
    </Button>
  )
}
