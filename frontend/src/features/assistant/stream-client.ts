import { api, apiBase, authHeaders } from '@/lib/api-client'
import { parseAssistantEvent, readSseFrames } from './events'
import type { AssistantEvent, ConsentDecision } from './types'

export type StreamTurnOptions = {
  signal?: AbortSignal
  onEvent: (event: AssistantEvent) => void
}

/**
 * Opens one assistant turn and drives the caller's reducer with typed events.
 * Contract: docs/specs/nova-61-agentic-assistant-design.md §4.
 *
 * `fetch` + `ReadableStream` rather than `EventSource`, because Nova's bearer
 * token lives in Zustand and `EventSource` cannot send an Authorization header.
 * No auto-reconnect: a replayed stream could duplicate a tool call.
 */
export async function streamAssistantTurn(
  threadId: string,
  content: string,
  { signal, onEvent }: StreamTurnOptions
): Promise<void> {
  const response = await fetch(
    `${apiBase()}/assistant/threads/${encodeURIComponent(threadId)}/messages`,
    {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }),
      body: JSON.stringify({ content }),
      signal,
    }
  )

  if (response.status === 401) {
    throw new Error('Session expired')
  }
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined)
    throw new Error(detail || 'The assistant request failed')
  }
  if (!response.body) {
    throw new Error('The assistant returned an empty stream')
  }

  for await (const frame of readSseFrames(response.body)) {
    const event = parseAssistantEvent(frame.event, frame.data)
    if (event) onEvent(event)
  }
}

/**
 * Consent is a separate HTTP call, not a frame on the stream (§4, §6). The
 * still-open stream then emits `tool_status` once the decision is applied.
 */
export async function decideToolCall(
  threadId: string,
  toolCallId: string,
  decision: ConsentDecision,
  alwaysAllow = false
): Promise<void> {
  await api.post(
    `/assistant/threads/${encodeURIComponent(threadId)}/tool-calls/${encodeURIComponent(toolCallId)}/decision`,
    { decision, always_allow: alwaysAllow }
  )
}
