import { useCallback, useRef, useState } from 'react'
import { streamAssistantTurn, decideToolCall, type TurnContext } from './stream-client'
import { resetGrant } from './thread-client'
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
  /**
   * Transcript store to drive. The provider injects one per conversation
   * binding so switching bindings can restore a previous conversation's
   * messages; standalone callers get their own.
   */
  transcript?: ReturnType<typeof useAssistantTranscript>
}

/** The parts of a conversation that must survive a binding switch. */
export type AssistantTurnSnapshot = {
  threadId: string | null
  grantActive: boolean
}

/**
 * Drives one assistant turn: user message, SSE events, stop, and consent.
 * The panel renders `messages` and passes `onDecide`; the workspace supplies
 * `ensureThread` so thread creation stays bound to the active file.
 */
export function useAssistantTurn({
  ensureThread,
  context,
  onError,
  transcript: injectedTranscript,
}: AssistantTurnOptions) {
  const ownTranscript = useAssistantTranscript()
  const transcript = injectedTranscript ?? ownTranscript
  const [streaming, setStreaming] = useState(false)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [decidingToolCallId, setDecidingToolCallId] = useState<string | null>(null)
  const [threadId, setThreadId] = useState<string | null>(null)
  const [grantActive, setGrantActive] = useState(false)
  const [resettingGrant, setResettingGrant] = useState(false)
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
        const result = await decideToolCall(toolCallId, decision, alwaysAllow)
        setGrantActive(result.grant_active)
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

  const resetPermissions = useCallback(async () => {
    if (!threadId || resettingGrant) return
    setResettingGrant(true)
    try {
      await resetGrant(threadId)
      setGrantActive(false)
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'The conversation permissions were not reset'
      transcript.applyEvent({ type: 'error', code: 'consent', message })
      onError?.(message)
    } finally {
      setResettingGrant(false)
    }
  }, [onError, resettingGrant, threadId, transcript])

  /**
   * Reads the conversation's thread/grant binding so a caller that keeps one
   * turn per binding identity can restore it after switching away and back.
   * Aborts any in-flight stream first: a snapshot must describe a settled
   * conversation, never one mid-turn.
   */
  const snapshot = useCallback((): AssistantTurnSnapshot => {
    abortRef.current?.abort()
    abortRef.current = null
    return { threadId, grantActive }
  }, [grantActive, threadId])

  const restore = useCallback((snapshot: AssistantTurnSnapshot) => {
    abortRef.current?.abort()
    abortRef.current = null
    setThreadId(snapshot.threadId)
    setGrantActive(snapshot.grantActive)
    setStreaming(false)
    setStatusMessage(null)
    setDecidingToolCallId(null)
  }, [])

  return {
    threadId,
    messages: transcript.messages,
    sendMessage,
    stop,
    decide,
    grantActive,
    resetPermissions,
    resettingGrant,
    streaming,
    statusMessage,
    decidingToolCallId,
    snapshot,
    restore,
  }
}
