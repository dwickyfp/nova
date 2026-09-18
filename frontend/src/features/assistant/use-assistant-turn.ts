import { useCallback, useRef, useState } from 'react'
import { streamAssistantTurn, decideToolCall, type TurnContext } from './stream-client'
import type { ToolCallDecision } from './tool-call-card'
import type { AssistantEvent } from './types'
import { useAssistantTranscript } from './use-assistant-transcript'

const STATUS_TEXT: Partial<Record<AssistantEvent['type'], string>> = {
  tool_call: 'The assistant is waiting for your approval.',
  done: 'The assistant finished responding.',
  error: 'The assistant ran into an error.',
}

export type AssistantTurnOptions = {
  /**
   * Resolves the thread to send into, creating one on first use. Returning
   * null aborts the turn (for example, no file is open).
   */
  ensureThread: () => Promise<string | null>
  /** Active worksheet context for this turn. */
  context?: TurnContext
  onError?: (message: string) => void
}

/**
 * Drives one assistant turn: user message, SSE events, stop, and consent.
 * The panel renders `messages` and passes `onDecide`; the workspace supplies
 * `ensureThread` so thread creation stays bound to the active file.
 */
export function useAssistantTurn({ ensureThread, context, onError }: AssistantTurnOptions) {
  const transcript = useAssistantTranscript()
  const [streaming, setStreaming] = useState(false)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [decidingToolCallId, setDecidingToolCallId] = useState<string | null>(null)
  const [threadId, setThreadId] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const sendMessage = useCallback(
    async (message: string) => {
      if (streaming) return
      const thread = threadId ?? (await ensureThread())
      if (!thread) {
        onError?.('Open or create a SQL file before asking the assistant')
        return
      }
      if (thread !== threadId) setThreadId(thread)

      transcript.addUserMessage(message)
      const controller = new AbortController()
      abortRef.current = controller
      setStreaming(true)
      setStatusMessage('The assistant is responding.')
      try {
        await streamAssistantTurn(thread, message, {
          signal: controller.signal,
          database: context?.database,
          schema: context?.schema,
          role: context?.role,
          onEvent: (event) => {
            transcript.applyEvent(event)
            const status = STATUS_TEXT[event.type]
            if (status) setStatusMessage(status)
          },
        })
        if (controller.signal.aborted) {
          transcript.markCancelled()
          setStatusMessage('Response stopped.')
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          transcript.markCancelled()
          setStatusMessage('Response stopped.')
        } else {
          const message = error instanceof Error ? error.message : 'The assistant failed'
          transcript.applyEvent({ type: 'error', code: 'transport', message })
          onError?.(message)
        }
      } finally {
        abortRef.current = null
        setStreaming(false)
      }
    },
    [context?.database, context?.role, context?.schema, ensureThread, onError, streaming, threadId, transcript]
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const decide = useCallback(
    async ({ toolCallId, decision, alwaysAllow }: ToolCallDecision) => {
      setDecidingToolCallId(toolCallId)
      try {
        await decideToolCall(toolCallId, decision, alwaysAllow)
      } catch (error) {
        const message = error instanceof Error ? error.message : 'The decision was not applied'
        transcript.applyEvent({ type: 'error', code: 'consent', message })
        onError?.(message)
      } finally {
        setDecidingToolCallId(null)
      }
    },
    [onError, transcript]
  )

  return {
    threadId,
    messages: transcript.messages,
    sendMessage,
    stop,
    decide,
    streaming,
    statusMessage,
    decidingToolCallId,
    reset: transcript.reset,
  }
}
