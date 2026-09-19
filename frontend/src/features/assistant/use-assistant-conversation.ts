import { useEffect, useRef } from 'react'
import type { TurnContext } from './stream-client'
import { useAssistantTurn } from './use-assistant-turn'

export type AssistantConversationOptions = {
  /**
   * Resolves the thread for the current binding, creating one on first use.
   * Returning null aborts the turn (for example, no file is open).
   */
  ensureThread: () => Promise<string | null>
  /** Active worksheet context for the next turn. */
  context: TurnContext
  /**
   * Identity of the current conversation binding. When it changes the
   * transcript resets and a fresh conversation starts, because a thread is
   * bound to one context (workspace file) and reusing it would misstate the
   * context of the messages already in it.
   */
  bindingKey: string | null
  onError?: (message: string) => void
}

/**
 * The store-agnostic seam between a conversation surface and the turn driver.
 * It owns only the binding lifecycle: reset the transcript and start the next
 * conversation when `bindingKey` changes. Thread creation and turn context stay
 * with the caller, so the same hook backs both the workspace file binding and
 * the global panel.
 */
export function useAssistantConversation({
  ensureThread,
  context,
  bindingKey,
  onError,
}: AssistantConversationOptions) {
  const assistant = useAssistantTurn({ ensureThread, context, onError })
  const { startConversation } = assistant
  const startConversationRef = useRef(startConversation)
  startConversationRef.current = startConversation

  const bindingRef = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    if (bindingRef.current === bindingKey) return
    bindingRef.current = bindingKey
    void startConversationRef.current()
  }, [bindingKey])

  return assistant
}
