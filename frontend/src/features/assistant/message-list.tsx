import { cn } from '@/lib/utils'
import { ToolCallCard, type ToolCallCardProps } from './tool-call-card'
import type { TranscriptMessage } from './use-assistant-transcript'

export type MessageListProps = {
  messages: TranscriptMessage[]
  onDecide?: ToolCallCardProps['onDecide']
  /** Set while a decision request is in flight for the pending tool call. */
  decidingToolCallId?: string | null
  /** Announced to assistive tech on state transitions, never per token. */
  statusMessage?: string | null
}

export function MessageList({
  messages,
  onDecide,
  decidingToolCallId,
  statusMessage,
}: MessageListProps) {
  return (
    <div className='flex flex-col gap-3'>
      <p role='status' aria-live='polite' className='sr-only'>
        {statusMessage ?? ''}
      </p>
      {messages.map((message) => (
        <MessageBubble
          key={message.message_id}
          message={message}
          onDecide={onDecide}
          busy={decidingToolCallId === message.tool_call_id}
        />
      ))}
    </div>
  )
}

const TURN_MARKER: Record<NonNullable<TranscriptMessage['turn_state']>, string | null> = {
  streaming: null,
  done: null,
  cancelled: 'Stopped before the answer finished.',
  error: null,
}

function MessageBubble({
  message,
  onDecide,
  busy,
}: {
  message: TranscriptMessage
  onDecide?: ToolCallCardProps['onDecide']
  busy?: boolean
}) {
  if (message.role === 'tool' && message.tool_call) {
    return (
      <ToolCallCard
        toolCall={message.tool_call}
        toolCallId={message.tool_call_id}
        onDecide={onDecide}
        busy={busy}
      />
    )
  }

  const isUser = message.role === 'user'
  const marker = message.turn_state ? TURN_MARKER[message.turn_state] : null

  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        data-role={message.role}
        className={cn(
          'max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap',
          isUser
            ? 'bg-primary text-primary-foreground'
            : 'border bg-surface-2 text-foreground',
          message.turn_state === 'error' && 'border-destructive/40 text-destructive'
        )}
      >
        {message.content}
        {marker ? (
          <span className='mt-1 block text-xs text-muted-foreground italic'>{marker}</span>
        ) : null}
      </div>
    </div>
  )
}
