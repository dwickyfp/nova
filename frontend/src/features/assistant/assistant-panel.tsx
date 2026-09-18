import { Bot, PanelRightClose, SendHorizontal, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { MessageList } from './message-list'
import type { ToolCallCardProps } from './tool-call-card'
import type { TranscriptMessage } from './use-assistant-transcript'
import { useIsNarrowForAssistant } from './use-assistant-panel'

export type AssistantPanelProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Overrides the built-in transcript rendering when supplied. */
  children?: React.ReactNode
  onSendMessage?: (message: string) => void
  disabled?: boolean
  /** Transcript state. When omitted the panel shows the empty state. */
  messages?: TranscriptMessage[]
  onDecide?: ToolCallCardProps['onDecide']
  decidingToolCallId?: string | null
  /** True while a turn is streaming; swaps Send for Stop. */
  streaming?: boolean
  onStop?: () => void
  /** Announced to assistive tech on state transitions, never per token. */
  statusMessage?: string | null
}

const PANEL_WIDTH = 'w-[22rem]'

function AssistantBody({
  children,
  onSendMessage,
  disabled,
  messages,
  onDecide,
  decidingToolCallId,
  streaming,
  onStop,
  statusMessage,
}: AssistantPanelProps) {
  const hasTranscript = Boolean(children) || Boolean(messages?.length)

  return (
    <div className='flex min-h-0 flex-1 flex-col'>
      <ScrollArea className='min-h-0 flex-1'>
        <div className='flex min-h-full flex-col p-3'>
          {children ??
            (hasTranscript ? (
              <MessageList
                messages={messages ?? []}
                onDecide={onDecide}
                decidingToolCallId={decidingToolCallId}
                statusMessage={statusMessage}
              />
            ) : (
              <EmptyState
                icon={Bot}
                title='Ask about this workspace'
                description='The assistant can explain schema, draft dialect-aware SQL, and run read-only queries with your approval.'
              />
            ))}
        </div>
      </ScrollArea>
      <form
        className='border-t p-3'
        onSubmit={(event) => {
          event.preventDefault()
          const form = event.currentTarget
          const field = form.elements.namedItem('assistant-message') as HTMLTextAreaElement | null
          const value = field?.value.trim()
          if (!value || streaming || disabled || !onSendMessage) return
          onSendMessage(value)
          if (field) field.value = ''
        }}
      >
        {disabled ? (
          <p className='mb-2 text-xs text-muted-foreground'>
            The assistant backend is not connected yet. This panel is read-only until it is.
          </p>
        ) : null}
        <div className='flex items-end gap-2'>
          <Textarea
            name='assistant-message'
            rows={2}
            placeholder='Ask a question or describe a query'
            disabled={disabled || !onSendMessage}
            className='min-h-9 flex-1 resize-none'
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                event.currentTarget.form?.requestSubmit()
              }
            }}
          />
          {streaming ? (
            <Button
              type='button'
              size='icon'
              variant='outline'
              onClick={onStop}
              aria-label='Stop generating'
            >
              <Square className='size-4' />
            </Button>
          ) : (
            <Button
              type='submit'
              size='icon'
              disabled={disabled || !onSendMessage}
              aria-label='Send message'
            >
              <SendHorizontal className='size-4' />
            </Button>
          )}
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

export function AssistantPanel(props: AssistantPanelProps) {
  const { open, onOpenChange } = props
  const isNarrow = useIsNarrowForAssistant()

  if (isNarrow) {
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side='right' className={cn('p-0', PANEL_WIDTH, 'sm:max-w-[22rem]')}>
          <SheetTitle className='sr-only'>Assistant</SheetTitle>
          <div className='flex h-full min-h-0 flex-col'>
            <AssistantHeader onClose={() => onOpenChange(false)} />
            <AssistantBody {...props} />
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
      <AssistantBody {...props} />
    </aside>
  )
}
