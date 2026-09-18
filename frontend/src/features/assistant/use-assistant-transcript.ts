import { useCallback, useState } from 'react'
import type { AssistantEvent, AssistantMessage, ToolCallView, TurnState } from './types'

export type TranscriptMessage = AssistantMessage & {
  turn_state?: TurnState
  /** Client-side correlation for tool_status. Not part of the wire shape. */
  tool_call_id?: string
}

let localId = 0
function nextLocalId(prefix: string) {
  localId += 1
  return `${prefix}-${localId}`
}

type Action =
  | { type: 'user_message'; content: string }
  | { type: 'event'; event: AssistantEvent }
  | { type: 'cancelled' }
  | { type: 'reset' }

function appendAssistantText(messages: TranscriptMessage[], text: string): TranscriptMessage[] {
  const last = messages[messages.length - 1]
  if (last && last.role === 'assistant' && last.turn_state === 'streaming') {
    return [...messages.slice(0, -1), { ...last, content: last.content + text }]
  }
  return [
    ...messages,
    {
      message_id: nextLocalId('assistant'),
      role: 'assistant',
      content: text,
      tool_call: null,
      created_at: new Date().toISOString(),
      turn_state: 'streaming',
    },
  ]
}

function upsertToolCall(
  messages: TranscriptMessage[],
  toolCallId: string,
  view: ToolCallView
): TranscriptMessage[] {
  const index = messages.findIndex((message) => message.tool_call_id === toolCallId)
  if (index === -1) {
    return [
      ...messages,
      {
        message_id: `tool-${toolCallId}`,
        role: 'tool',
        content: '',
        tool_call: view,
        tool_call_id: toolCallId,
        created_at: new Date().toISOString(),
      },
    ]
  }
  return [
    ...messages.slice(0, index),
    { ...messages[index], tool_call: view },
    ...messages.slice(index + 1),
  ]
}

function applyToolStatus(
  messages: TranscriptMessage[],
  toolCallId: string,
  status: ToolCallView['status']
): TranscriptMessage[] {
  return messages.map((message) =>
    message.tool_call_id === toolCallId && message.tool_call
      ? { ...message, tool_call: { ...message.tool_call, status } }
      : message
  )
}

function applyEvent(state: TranscriptMessage[], event: AssistantEvent): TranscriptMessage[] {
  switch (event.type) {
    case 'text_delta':
      return appendAssistantText(state, event.text)
    case 'tool_call':
      return upsertToolCall(state, event.payload.tool_call_id, {
        tool_name: event.payload.tool_name,
        sql_preview: event.payload.sql_preview,
        classification: event.payload.classification,
        status: event.payload.status,
        result_summary: event.payload.result_summary,
        error: event.payload.error,
      })
    case 'tool_status':
      return applyToolStatus(state, event.tool_call_id, event.status)
    case 'done':
      return state.map((message) =>
        message.role === 'assistant' && message.turn_state === 'streaming'
          ? { ...message, message_id: event.message_id, turn_state: 'done' }
          : message
      )
    case 'error':
      return [
        ...state,
        {
          message_id: nextLocalId('error'),
          role: 'assistant',
          content: event.message,
          tool_call: null,
          created_at: new Date().toISOString(),
          turn_state: 'error',
        },
      ]
    case 'ping':
      return state
  }
}

export function transcriptReducer(state: TranscriptMessage[], action: Action): TranscriptMessage[] {
  switch (action.type) {
    case 'user_message':
      return [
        ...state,
        {
          message_id: nextLocalId('user'),
          role: 'user',
          content: action.content,
          tool_call: null,
          created_at: new Date().toISOString(),
        },
      ]
    case 'event':
      return applyEvent(state, action.event)
    case 'cancelled':
      return state.map((message) =>
        message.turn_state === 'streaming' ? { ...message, turn_state: 'cancelled' } : message
      )
    case 'reset':
      return []
  }
}

export function useAssistantTranscript() {
  const [messages, setMessages] = useState<TranscriptMessage[]>([])

  const addUserMessage = useCallback(
    (content: string) => setMessages((prev) => transcriptReducer(prev, { type: 'user_message', content })),
    []
  )
  const applyEvent = useCallback(
    (event: AssistantEvent) => setMessages((prev) => transcriptReducer(prev, { type: 'event', event })),
    []
  )
  const markCancelled = useCallback(
    () => setMessages((prev) => transcriptReducer(prev, { type: 'cancelled' })),
    []
  )
  const reset = useCallback(() => setMessages((prev) => transcriptReducer(prev, { type: 'reset' })), [])

  return { messages, addUserMessage, applyEvent, markCancelled, reset }
}
