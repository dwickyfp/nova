import type {
  AssistantEvent,
  PlanStep,
  ThinkingPhase,
  ThinkingStatus,
  ToolCallStatus,
  ToolClassification,
} from './types'

/**
 * Parses one SSE frame into a typed event. Returns null for frames the client
 * has no contract for, so an added server event cannot crash the transcript.
 * Contract: docs/specs/nova-61-agentic-assistant-design.md §4.
 */
export function parseAssistantEvent(eventName: string, data: string): AssistantEvent | null {
  let payload: unknown
  try {
    payload = data ? JSON.parse(data) : {}
  } catch {
    return null
  }
  const record = payload as Record<string, unknown>
  switch (eventName) {
    case 'text_delta':
      return typeof record.text === 'string' ? { type: 'text_delta', text: record.text } : null
    case 'thinking':
      return parseThinking(record)
    case 'plan':
      return parsePlan(record)
    case 'tool_call':
      return parseToolCall(record)
    case 'tool_status': {
      const id = record.tool_call_id
      const status = record.status
      if (typeof id !== 'string' || !isToolCallStatus(status)) return null
      return { type: 'tool_status', tool_call_id: id, status }
    }
    case 'done':
      return typeof record.message_id === 'string' && typeof record.finish_reason === 'string'
        ? { type: 'done', message_id: record.message_id, finish_reason: record.finish_reason }
        : null
    case 'error':
      return typeof record.code === 'string' && typeof record.message === 'string'
        ? { type: 'error', code: record.code, message: record.message }
        : null
    case 'ping':
      return { type: 'ping' }
    default:
      return null
  }
}

const STATUSES: ToolCallStatus[] = [
  'pending',
  'approved',
  'denied',
  'running',
  'done',
  'failed',
  'cancelled',
]

const CLASSIFICATIONS: ToolClassification[] = ['read_only', 'destructive', 'denied']

export function isToolCallStatus(value: unknown): value is ToolCallStatus {
  return typeof value === 'string' && (STATUSES as string[]).includes(value)
}

export function isToolClassification(value: unknown): value is ToolClassification {
  return typeof value === 'string' && (CLASSIFICATIONS as string[]).includes(value)
}

const THINKING_PHASES: ThinkingPhase[] = ['plan', 'skill', 'act', 'observe', 'answer']
const THINKING_STATUSES: ThinkingStatus[] = ['running', 'done']

export function isThinkingPhase(value: unknown): value is ThinkingPhase {
  return typeof value === 'string' && (THINKING_PHASES as string[]).includes(value)
}

function parseThinking(record: Record<string, unknown>): AssistantEvent | null {
  if (
    !isThinkingPhase(record.phase) ||
    typeof record.text !== 'string' ||
    typeof record.status !== 'string' ||
    !(THINKING_STATUSES as string[]).includes(record.status)
  ) {
    return null
  }
  return {
    type: 'thinking',
    phase: record.phase,
    text: record.text,
    status: record.status as ThinkingStatus,
  }
}

function parsePlan(record: Record<string, unknown>): AssistantEvent | null {
  const raw = record.steps
  if (!Array.isArray(raw)) return null
  const steps: PlanStep[] = []
  for (const entry of raw) {
    if (
      typeof entry !== 'object' ||
      entry === null ||
      typeof (entry as PlanStep).id !== 'string' ||
      typeof (entry as PlanStep).text !== 'string' ||
      typeof (entry as PlanStep).status !== 'string'
    ) {
      return null
    }
    steps.push(entry as PlanStep)
  }
  return { type: 'plan', steps }
}

function parseToolCall(record: Record<string, unknown>): AssistantEvent | null {
  const id = record.tool_call_id
  const toolName = record.tool_name
  const preview = record.sql_preview
  const classification = record.classification
  const status = record.status
  if (
    typeof id !== 'string' ||
    typeof toolName !== 'string' ||
    typeof preview !== 'string' ||
    !isToolClassification(classification) ||
    !isToolCallStatus(status)
  ) {
    return null
  }
  const resultSummary = typeof record.result_summary === 'string' ? record.result_summary : null
  const error = typeof record.error === 'string' ? record.error : null
  return {
    type: 'tool_call',
    payload: {
      tool_call_id: id,
      tool_name: toolName,
      sql_preview: preview,
      classification,
      status,
      result_summary: resultSummary,
      error,
    },
  }
}

export type SseFrame = { event: string; data: string }

/**
 * Splits a byte stream into SSE frames. Handles the event/data field pair the
 * backend emits and flushes a trailing frame without a terminating blank line,
 * which is what a cancelled stream produces.
 */
export async function* readSseFrames(
  body: ReadableStream<Uint8Array>
): AsyncGenerator<SseFrame> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const parseBlock = (block: string): SseFrame | null => {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    return dataLines.length ? { event, data: dataLines.join('\n') } : null
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let separator = buffer.indexOf('\n\n')
      while (separator !== -1) {
        const block = buffer.slice(0, separator)
        buffer = buffer.slice(separator + 2)
        const frame = parseBlock(block)
        if (frame) yield frame
        separator = buffer.indexOf('\n\n')
      }
    }
    if (buffer.trim()) {
      const frame = parseBlock(buffer)
      if (frame) yield frame
    }
  } finally {
    reader.releaseLock()
  }
}
