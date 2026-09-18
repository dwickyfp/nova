import { Bot, PanelRightClose, SendHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { useIsNarrowForAssistant } from './use-assistant-panel'

export type AssistantPanelProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Rendered transcript. T-D2 supplies the streaming message list here. */
  children?: React.ReactNode
  /** Placeholder shown until a conversation has any messages. */
  onSendMessage?: (message: string) => void
  disabled?: boolean
}

const PANEL_WIDTH = 'w-[22rem]'

function AssistantBody({
  children,
  onSendMessage,
  disabled,
}: Pick<AssistantPanelProps, 'children' | 'onSendMessage' | 'disabled'>) {
  return (
    <div className='flex min-h-0 flex-1 flex-col'>
      <ScrollArea className='min-h-0 flex-1'>
        <div className='flex min-h-full flex-col p-3'>
          {children ?? (
            <EmptyState
              icon={Bot}
              title='Ask about this workspace'
              description='The assistant can explain schema, draft dialect-aware SQL, and run read-only queries with your approval.'
            />
          )}
        </div>
      </ScrollArea>
      <form
        className='border-t p-3'
        onSubmit={(event) => {
          event.preventDefault()
          const form = event.currentTarget
          const field = form.elements.namedItem('assistant-message') as HTMLTextAreaElement | null
          const value = field?.value.trim()
          if (!value) return
          onSendMessage?.(value)
          if (field) field.value = ''
        }}
      >
        <div className='flex items-end gap-2'>
          <Textarea
            name='assistant-message'
            rows={2}
            placeholder='Ask a question or describe a query'
            disabled={disabled}
            className='min-h-9 flex-1 resize-none'
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                event.currentTarget.form?.requestSubmit()
              }
            }}
          />
          <Button type='submit' size='icon' disabled={disabled} aria-label='Send message'>
            <SendHorizontal className='size-4' />
          </Button>
        </div>
      </form>
    </div>
  )
}

function AssistantHeader({ onClose }: { onClose: () => void }) {
  return (
    <div className='flex items-center justify-between gap-2 border-b px-3 py-2'>
      <div className='flex items-center gap-2'>
        <Bot aria-hidden='true' className='size-4 text-muted-foreground' />
        <h2 className='text-sm font-medium'>Assistant</h2>
      </div>
      <Button
        type='button'
        variant='ghost'
        size='icon'
        onClick={onClose}
        aria-label='Close assistant'
      >
        <PanelRightClose className='size-4' />
      </Button>
    </div>
  )
}

export function AssistantPanel({
  open,
  onOpenChange,
  children,
  onSendMessage,
  disabled,
}: AssistantPanelProps) {
  const isNarrow = useIsNarrowForAssistant()

  if (isNarrow) {
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side='right' className={cn('p-0', PANEL_WIDTH, 'sm:max-w-[22rem]')}>
          <SheetTitle className='sr-only'>Assistant</SheetTitle>
          <div className='flex h-full min-h-0 flex-col'>
            <AssistantHeader onClose={() => onOpenChange(false)} />
            <AssistantBody onSendMessage={onSendMessage} disabled={disabled}>
              {children}
            </AssistantBody>
          </div>
        </SheetContent>
      </Sheet>
    )
  }

  if (!open) return null

  return (
    <aside
      id='assistant-panel'
      aria-label='Assistant'
      className={cn('flex min-h-0 shrink-0 flex-col border-l bg-background', PANEL_WIDTH)}
    >
      <AssistantHeader onClose={() => onOpenChange(false)} />
      <AssistantBody onSendMessage={onSendMessage} disabled={disabled}>
        {children}
      </AssistantBody>
    </aside>
  )
}
