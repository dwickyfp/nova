/**
 * Client mirror of the frozen NOVA-61 T-A0 contract.
 * Sources: docs/specs/nova-61-agentic-assistant-design.md §2.1 and §4.
 * Keep these names in lockstep with the backend; the SSE parser and the UI
 * both depend on them.
 */

export type ToolCallStatus =
  | 'pending'
  | 'approved'
  | 'denied'
  | 'running'
  | 'done'
  | 'failed'
  | 'cancelled'

export type ToolClassification = 'read_only' | 'destructive' | 'denied'

export type ToolCallView = {
  tool_name: string
  /** Redacted SQL only. The backend guarantees this before the event is sent. */
  sql_preview: string
  classification: ToolClassification
  status: ToolCallStatus
  /** Row count / affected / error text. Never rows. */
  result_summary?: string | null
  error?: string | null
}

export type AssistantMessage = {
  message_id: string
  /**
   * `activity` is a client-only role carrying the agentic trace (plan +
   * thinking steps). The backend never emits it.
   */
  role: 'user' | 'assistant' | 'tool' | 'activity'
  content: string
  tool_call: ToolCallView | null
  created_at: string
}

/** The agentic phases a thinking frame can carry. */
export type ThinkingPhase = 'plan' | 'skill' | 'act' | 'observe' | 'answer'

export type ThinkingStatus = 'running' | 'done'

export type PlanStep = {
  id: string
  text: string
  status: 'pending' | 'running' | 'done'
}

export type AssistantEvent =
  | { type: 'text_delta'; text: string }
  | { type: 'thinking'; phase: ThinkingPhase; text: string; status: ThinkingStatus }
  | { type: 'plan'; steps: PlanStep[] }
  | { type: 'tool_call'; payload: ToolCallView & { tool_call_id: string } }
  | { type: 'tool_status'; tool_call_id: string; status: ToolCallStatus }
  | { type: 'done'; message_id: string; finish_reason: string }
  | { type: 'error'; code: string; message: string }
  | { type: 'ping' }

/** UI intent: what the user chose in the card, before the read-only gate. */
export type ConsentDecision = 'approve' | 'deny'

/** Wire enum frozen in spec §6.1. The backend rejects `approve`/`deny`. */
export type ConsentDecisionPayload = 'allow_once' | 'allow_session' | 'deny'

/** Outcome of a turn, so the transcript can mark a cancelled partial answer. */
export type TurnState = 'streaming' | 'done' | 'cancelled' | 'error'
