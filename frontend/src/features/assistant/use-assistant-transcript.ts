import { useCallback, useState } from 'react'
import type { AttachedQuery } from './query-attach'
import type {
  AssistantEvent,
  AssistantMessage,
  PlanStep,
  ThinkingPhase,
  ThinkingStatus,
  ToolCallView,
  TurnState,
} from './types'

/** One observed agentic step, kept so the block renders the whole trace. */
export type ActivityStep = {
  key: string
  phase: ThinkingPhase
  text: string
  status: ThinkingStatus
}

export type TranscriptMessage = AssistantMessage & {
  turn_state?: TurnState
  /** Client-side correlation for tool_status. Not part of the wire shape. */
  tool_call_id?: string
  /**
   * Client-side only: the agentic trace for a turn (plan + thinking steps), on
   * a message with `role: 'activity'`. Never part of the wire contract.
   */
  activity_steps?: ActivityStep[]
  activity_plan?: PlanStep[]
  /**
   * Client-side only: queries attached to a user message. `content` carries the
   * serialized prompt the model received (attachment preamble + typed text);
   * these let the bubble render the attachments as cards and show only the text
   * the user actually typed. Absent on a transcript restored from the server,
   * where the wire text is all that is available.
   */
  attachments?: AttachedQuery[]
  /** The user's typed text without the attachment preamble. */
  display_text?: string
}

let localId = 0
function nextLocalId(prefix: string) {
  localId += 1
  return `${prefix}-${localId}`
}

type Action =
  | { type: 'user_message'; content: string; attachments?: AttachedQuery[]; displayText?: string }
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

/**
 * The activity block for the current turn: the plan plus the thinking steps.
 *
 * One block per turn, kept at the tail so it sits immediately above the answer
 * the model is writing. A new turn (after a user message) starts a new block, so
 * the trace stays attached to the turn that produced it.
 */
function upsertActivity(
  state: TranscriptMessage[],
  update: (steps: ActivityStep[], plan: PlanStep[]) => Partial<TranscriptMessage>
): TranscriptMessage[] {
  const index = state.findIndex((message) => message.role === 'activity')
  if (index === -1) {
    const block: TranscriptMessage = {
      message_id: nextLocalId('activity'),
      role: 'activity',
      content: '',
      tool_call: null,
      created_at: new Date().toISOString(),
      activity_steps: [],
      activity_plan: [],
      ...update([], []),
    }
    return [...state, block]
  }
  const current = state[index]
  return [
    ...state.slice(0, index),
    {
      ...current,
      ...update(current.activity_steps ?? [], current.activity_plan ?? []),
    },
    ...state.slice(index + 1),
  ]
}

function applyEvent(state: TranscriptMessage[], event: AssistantEvent): TranscriptMessage[] {
  switch (event.type) {
    case 'text_delta':
      return appendAssistantText(state, event.text)
    case 'thinking':
      return upsertActivity(state, (steps) => ({
        activity_steps: [
          ...steps,
          {
            key: nextLocalId('step'),
            phase: event.phase,
            text: event.text,
            status: event.status,
          },
        ],
      }))
    case 'plan':
      return upsertActivity(state, () => ({ activity_plan: event.steps }))
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
      return state.map((message) => {
        if (message.role === 'assistant' && message.turn_state === 'streaming') {
          return { ...message, message_id: event.message_id, turn_state: 'done' }
        }
        // No step may be left spinning once the turn ends.
        if (message.role === 'activity' && message.activity_steps) {
          return {
            ...message,
            activity_steps: message.activity_steps.map((step) =>
              step.status === 'running' ? { ...step, status: 'done' } : step
            ),
            activity_plan: message.activity_plan?.map((step) =>
              step.status === 'pending' || step.status === 'running'
                ? { ...step, status: 'done' }
                : step
            ),
          }
        }
        return message
      })
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
          attachments: action.attachments,
          display_text: action.displayText,
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
    (content: string, attachments?: AttachedQuery[], displayText?: string) =>
      setMessages((prev) =>
        transcriptReducer(prev, { type: 'user_message', content, attachments, displayText })
      ),
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
  const replace = useCallback((next: TranscriptMessage[]) => setMessages(next), [])

  return { messages, addUserMessage, applyEvent, markCancelled, reset, replace }
}
