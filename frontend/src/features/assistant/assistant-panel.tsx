import { Bot, PanelRightClose, SendHorizontal, ShieldCheck, Square } from 'lucide-react'
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
  /** True while a read-only always-allow grant covers this conversation. */
  grantActive?: boolean
  onResetPermissions?: () => void
  /** Disables the reset control while the revoke request is in flight. */
  resettingPermissions?: boolean
}

const PANEL_WIDTH = 'w-[22rem]'

function ResetPermissionsBar({
  active,
  onReset,
  resetting,
}: {
  active: boolean
  onReset: () => void
  resetting: boolean
}) {
  if (!active) return null

  return (
    <div className='flex items-center justify-between gap-2 border-b bg-surface-2 px-3 py-2'>
      <p className='flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground'>
        <ShieldCheck aria-hidden='true' className='size-3.5 shrink-0 text-success-strong' />
        <span className='truncate'>Read-only queries are allowed in this conversation.</span>
      </p>
      <Button
        type='button'
        size='sm'
        variant='outline'
        className='min-h-11 shrink-0'
        disabled={resetting}
        onClick={onReset}
      >
        {resetting ? 'Resetting' : 'Reset permissions'}
      </Button>
    </div>
  )
}

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
  grantActive,
  onResetPermissions,
  resettingPermissions,
}: AssistantPanelProps) {
  const hasTranscript = Boolean(children) || Boolean(messages?.length)

  return (
    <div className='flex min-h-0 flex-1 flex-col'>
      <ResetPermissionsBar
        active={Boolean(grantActive)}
        onReset={() => onResetPermissions?.()}
        resetting={Boolean(resettingPermissions)}
      />
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

  return (
    // The wrapper animates its width so the panel slides rather than blinking
    // in and out. The aside stays mounted while closed, so `inert` (not
    // unmounting) is what keeps its transcript and controls out of the tab
    // order and off the accessibility tree.
    <div
      data-state={open ? 'open' : 'closed'}
      className={cn(
        'shrink-0 overflow-hidden transition-[width] duration-200 ease-in-out motion-reduce:transition-none',
        open ? PANEL_WIDTH : 'w-0'
      )}
    >
      <aside
        id='assistant-panel'
        aria-label='Assistant'
        inert={!open}
        className={cn('flex h-full min-h-0 flex-col border-l bg-background', PANEL_WIDTH)}
      >
        <AssistantHeader onClose={() => onOpenChange(false)} />
        <AssistantBody {...props} />
      </aside>
    </div>
  )
}
