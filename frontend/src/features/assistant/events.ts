import type {
  AssistantEvent,
  PlanStep,
  ThinkingPhase,
  ThinkingStatus,
  ToolCallStatus,
  ToolClassification,
} from "./types";

/**
 * Parses one SSE frame into a typed event. Returns null for frames the client
 * has no contract for, so an added server event cannot crash the transcript.
 * Contract: docs/specs/nova-61-agentic-assistant-design.md §4.
 */
export function parseAssistantEvent(
  eventName: string,
  data: string,
): AssistantEvent | null {
  let payload: unknown;
  try {
    payload = data ? JSON.parse(data) : {};
  } catch {
    return null;
  }
  const record = payload as Record<string, unknown>;
  const parsed = (() => {
    switch (eventName) {
      case "role_changed":
        return typeof record.active_role === "string" &&
          typeof record.security_context_version === "number"
          ? {
              type: "role_changed",
              active_role: record.active_role,
              security_context_version: record.security_context_version,
            }
          : null;
      case "text_delta":
        return typeof record.text === "string"
          ? {
              type: "text_delta",
              text: record.text,
              ...contentPosition(record),
            }
          : null;
      case "thinking":
        return parseThinking(record);
      case "plan":
        return parsePlan(record);
      case "tool_call":
        return parseToolCall(record);
      case "tool_progress": {
        const id = record.tool_call_id;
        const stage = record.stage;
        const text = record.text;
        if (
          typeof id !== "string" ||
          typeof stage !== "string" ||
          typeof text !== "string"
        ) {
          return null;
        }
        return {
          type: "tool_progress",
          tool_call_id: id,
          stage,
          text,
          sql_preview:
            typeof record.sql_preview === "string"
              ? record.sql_preview
              : undefined,
        };
      }
      case "tool_status": {
        const id = record.tool_call_id;
        const status = record.status;
        if (typeof id !== "string" || !isToolCallStatus(status)) return null;
        return { type: "tool_status", tool_call_id: id, status };
      }
      case "table":
        return parseTable(record);
      case "chart":
        return parseChart(record);
      case "citation":
        return parseCitation(record);
      case "content_block_done": {
        const index = record.content_index;
        const id = record.content_id;
        return typeof index === "number" && typeof id === "string"
          ? { type: "content_block_done", content_index: index, content_id: id }
          : null;
      }
      case "tool_detail": {
        const id = record.tool_call_id;
        const text = record.text;
        if (typeof id !== "string" || typeof text !== "string") return null;
        return { type: "tool_detail", tool_call_id: id, text };
      }
      case "done": {
        if (
          typeof record.message_id !== "string" ||
          typeof record.finish_reason !== "string"
        ) {
          return null;
        }
        // Usage is optional: a provider that reported none sends no field, and
        // the panel must show nothing rather than a zero it invented.
        const total = (record.usage as { total_tokens?: unknown } | undefined)
          ?.total_tokens;
        const usage = record.usage as { prompt_tokens?: unknown; completion_tokens?: unknown } | undefined;
        return {
          type: "done",
          message_id: record.message_id,
          finish_reason: record.finish_reason,
          total_tokens: typeof total === "number" ? total : undefined,
          prompt_tokens: typeof usage?.prompt_tokens === "number" ? usage.prompt_tokens : undefined,
          completion_tokens: typeof usage?.completion_tokens === "number" ? usage.completion_tokens : undefined,
        };
      }
      case "error":
        return typeof record.code === "string" &&
          typeof record.message === "string"
          ? { type: "error", code: record.code, message: record.message }
          : null;
      case "ping":
        return { type: "ping" };
      default:
        return null;
    }
  })();
  if (!parsed) return null;
  return { ...parsed, ...eventMetadata(record) } as AssistantEvent;
}

function eventMetadata(record: Record<string, unknown>): {
  run_id?: string;
  sequence?: number;
} {
  const metadata: { run_id?: string; sequence?: number } = {};
  if (typeof record.run_id === "string") metadata.run_id = record.run_id;
  if (typeof record.sequence === "number") metadata.sequence = record.sequence;
  return metadata;
}

const STATUSES: ToolCallStatus[] = [
  "pending",
  "approved",
  "denied",
  "running",
  "done",
  "failed",
  "cancelled",
];

const CLASSIFICATIONS: ToolClassification[] = [
  "read_only",
  "session_change",
  "destructive",
  "denied",
];

export function isToolCallStatus(value: unknown): value is ToolCallStatus {
  return typeof value === "string" && (STATUSES as string[]).includes(value);
}

export function isToolClassification(
  value: unknown,
): value is ToolClassification {
  return (
    typeof value === "string" && (CLASSIFICATIONS as string[]).includes(value)
  );
}

const THINKING_PHASES: ThinkingPhase[] = [
  "plan",
  "skill",
  "act",
  "observe",
  "answer",
];
const THINKING_STATUSES: ThinkingStatus[] = ["running", "done"];

export function isThinkingPhase(value: unknown): value is ThinkingPhase {
  return (
    typeof value === "string" && (THINKING_PHASES as string[]).includes(value)
  );
}

function parseThinking(record: Record<string, unknown>): AssistantEvent | null {
  if (
    !isThinkingPhase(record.phase) ||
    typeof record.text !== "string" ||
    typeof record.status !== "string" ||
    !(THINKING_STATUSES as string[]).includes(record.status)
  ) {
    return null;
  }
  return {
    type: "thinking",
    phase: record.phase,
    text: record.text,
    status: record.status as ThinkingStatus,
  };
}

function parsePlan(record: Record<string, unknown>): AssistantEvent | null {
  const raw = record.steps;
  if (!Array.isArray(raw)) return null;
  const steps: PlanStep[] = [];
  for (const entry of raw) {
    if (
      typeof entry !== "object" ||
      entry === null ||
      typeof (entry as PlanStep).id !== "string" ||
      typeof (entry as PlanStep).text !== "string" ||
      typeof (entry as PlanStep).status !== "string"
    ) {
      return null;
    }
    steps.push(entry as PlanStep);
  }
  return { type: "plan", steps };
}

function parseToolCall(record: Record<string, unknown>): AssistantEvent | null {
  const id = record.tool_call_id;
  const toolName = record.tool_name;
  const preview = record.sql_preview;
  const classification = record.classification;
  const status = record.status;
  if (
    typeof id !== "string" ||
    typeof toolName !== "string" ||
    typeof preview !== "string" ||
    !isToolClassification(classification) ||
    !isToolCallStatus(status)
  ) {
    return null;
  }
  const resultSummary =
    typeof record.result_summary === "string" ? record.result_summary : null;
  const error = typeof record.error === "string" ? record.error : null;
  return {
    type: "tool_call",
    payload: {
      tool_call_id: id,
      tool_name: toolName,
      sql_preview: preview,
      classification,
      status,
      result_summary: resultSummary,
      error,
    },
  };
}

function parseTable(record: Record<string, unknown>): AssistantEvent | null {
  const columns = record.columns;
  const rows = record.rows;
  if (!Array.isArray(columns) || !Array.isArray(rows)) return null;
  const title = typeof record.title === "string" ? record.title : undefined;
  return {
    type: "table",
    ...contentPosition(record),
    payload: {
      title,
      columns: columns.map((c) => String(c)),
      rows: rows as (string | number | null)[][],
      tool_call_id:
        typeof record.tool_call_id === "string"
          ? record.tool_call_id
          : undefined,
    },
  };
}

function parseChart(record: Record<string, unknown>): AssistantEvent | null {
  const toolCallId = record.tool_call_id;
  const spec = record.chart_spec;
  if (typeof toolCallId !== "string" || typeof spec !== "string") return null;
  return {
    type: "chart",
    ...contentPosition(record),
    payload: { tool_call_id: toolCallId, chart_spec: spec },
  };
}

function parseCitation(record: Record<string, unknown>): AssistantEvent | null {
  return {
    type: "citation",
    ...contentPosition(record),
    payload: {
      title: typeof record.title === "string" ? record.title : undefined,
      source: typeof record.source === "string" ? record.source : undefined,
      snippet: typeof record.snippet === "string" ? record.snippet : undefined,
    },
  };
}

function contentPosition(record: Record<string, unknown>): {
  content_index?: number;
  content_id?: string;
} {
  return {
    content_index:
      typeof record.content_index === "number"
        ? record.content_index
        : undefined,
    content_id:
      typeof record.content_id === "string" ? record.content_id : undefined,
  };
}

export type SseFrame = { event: string; data: string };

/**
 * Splits a byte stream into SSE frames. Handles the event/data field pair the
 * backend emits and flushes a trailing frame without a terminating blank line,
 * which is what a cancelled stream produces.
 */
export async function* readSseFrames(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const parseBlock = (block: string): SseFrame | null => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:"))
        dataLines.push(line.slice(5).trimStart());
    }
    return dataLines.length ? { event, data: dataLines.join("\n") } : null;
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let separator = buffer.indexOf("\n\n");
      while (separator !== -1) {
        const block = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);
        const frame = parseBlock(block);
        if (frame) yield frame;
        separator = buffer.indexOf("\n\n");
      }
    }
    if (buffer.trim()) {
      const frame = parseBlock(buffer);
      if (frame) yield frame;
    }
  } finally {
    reader.releaseLock();
  }
}
