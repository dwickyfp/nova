import { useCallback, useRef, useState } from 'react'
import { streamAssistantTurn, decideToolCall } from './stream-client'
import type { ToolCallDecision } from './tool-call-card'
import type { AssistantEvent } from './types'
import { useAssistantTranscript } from './use-assistant-transcript'

const STATUS_TEXT: Partial<Record<AssistantEvent['type'], string>> = {
  tool_call: 'The assistant is waiting for your approval.',
  done: 'The assistant finished responding.',
  error: 'The assistant ran into an error.',
}

export type AssistantTurnOptions = {
  threadId: string | null
  /** Called with the user text before the stream opens, so the API can put it in the thread. */
  onSendMessage?: (message: string) => void
  onError?: (message: string) => void
}

/**
 * Drives one assistant turn: user message, SSE events, stop, and consent.
 * The panel renders `messages` and passes `onDecide`; the workspace wires
 * `sendMessage` once the T-B1 endpoints exist.
 */
export function useAssistantTurn({ threadId, onSendMessage, onError }: AssistantTurnOptions) {
  const transcript = useAssistantTranscript()
  const [streaming, setStreaming] = useState(false)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [decidingToolCallId, setDecidingToolCallId] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const sendMessage = useCallback(
    async (message: string) => {
      if (!threadId || streaming) return
      transcript.addUserMessage(message)
      onSendMessage?.(message)
      const controller = new AbortController()
      abortRef.current = controller
      setStreaming(true)
      setStatusMessage('The assistant is responding.')
      try {
        await streamAssistantTurn(threadId, message, {
          signal: controller.signal,
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
    [onError, onSendMessage, streaming, threadId, transcript]
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const decide = useCallback(
    async ({ toolCallId, decision, alwaysAllow }: ToolCallDecision) => {
      if (!threadId) return
      setDecidingToolCallId(toolCallId)
      try {
        await decideToolCall(threadId, toolCallId, decision, alwaysAllow)
      } catch (error) {
        const message = error instanceof Error ? error.message : 'The decision was not applied'
        transcript.applyEvent({ type: 'error', code: 'consent', message })
        onError?.(message)
      } finally {
        setDecidingToolCallId(null)
      }
    },
    [onError, threadId, transcript]
  )

  return {
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
