/**
 * Studio stream protocol v2 (server → Studio chat).
 *
 * Every event carries the run's `run_id` and a monotonic `seq`. Parts open
 * with `part.start`, grow with `part.delta`, and seal with `part.end`; tools
 * report through `tool.status`. The server contract lives in
 * `backend/app/modules/assistant/events_v2.py`.
 *
 * Nested payloads (a tool call, a table, a chart, a guard decision, …) keep
 * the shapes the v1 parser already validates, so this parser delegates to it
 * rather than repeating those checks.
 */
import { parseAssistantEvent } from "@/features/assistant/events";
import type {
  AssistantEvent,
  ChartBlock,
  CitationBlock,
  TableBlock,
  ThinkingPhase,
  ToolCallStatus,
  ToolCallView,
} from "@/features/assistant/types";
import type { GuardDecision } from "@/features/agents/api";
import type { DashboardArtifactBlock } from "@/features/viz/types";
import type { UiBlock } from "../intelligent-ui/types";
import { hitlStatus, parseHitlRequest } from "./hitl/parse";
import type { HitlKind, HitlRequestSpec, HitlStatus } from "./hitl/types";

export const PROTOCOL_VERSION = 2;

type Envelope = { run_id?: string; seq: number };

export type PartStartFields =
  | { type: "reasoning"; source: "nova" | "model"; phase?: ThinkingPhase; text: string; planSteps?: number }
  | { type: "text"; contentIndex?: number; contentId?: string }
  | {
      type: "tool";
      toolCallId: string;
      toolName: string;
      /** The Mission run that made the call, when it is not this run. */
      originRunId?: string;
      skillName?: string;
      classification?: ToolCallView["classification"];
    }
  | { type: "guard"; decision: GuardDecision }
  | {
      type: "hitl_request";
      /** The server's id for the request; answers are posted to it. */
      requestId: string;
      kind: HitlKind;
      /** The tool part an approval decides. */
      toolPartId?: string;
    }
  | { type: "table"; block: TableBlock & { tool_call_id?: string }; contentIndex?: number; contentId?: string }
  | { type: "chart"; block: ChartBlock; contentIndex?: number; contentId?: string }
  | { type: "citation"; block: CitationBlock; contentIndex?: number; contentId?: string }
  | { type: "dashboard"; block: DashboardArtifactBlock; contentIndex?: number; contentId?: string }
  | { type: "ui"; block: Omit<UiBlock, "program">; contentIndex?: number; contentId?: string };

export type PartEndState = "done" | "error" | "cancelled";

export type StudioEvent = Envelope &
  (
    | { type: "run.started"; role: string; protocolVersion: number }
    | ({ type: "part.start"; partId: string } & { part: PartStartFields })
    | { type: "part.delta"; partId: string; text: string; replace: boolean }
    | { type: "part.end"; partId: string; state: PartEndState; final?: string }
    | {
        type: "tool.status";
        partId: string;
        status: ToolCallStatus;
        preview?: string;
        progress?: { stage: string; text: string };
        detail?: string;
        evidence?: Record<string, unknown>;
      }
    | {
        type: "tool.evidence";
        partId: string;
        event: Extract<AssistantEvent, { type: "evidence_health" | "evidence_envelope" }>;
      }
    | {
        type: "input.required";
        partId: string;
        requestId: string;
        request: HitlRequestSpec;
        /** ISO time after which the server stops waiting. */
        expiresAt?: string;
      }
    | {
        type: "input.resolved";
        partId: string;
        status: Exclude<HitlStatus, "pending">;
        summary: string;
        answer?: Record<string, unknown>;
      }
    | { type: "role.changed"; activeRole: string }
    | {
        type: "run.finished";
        messageId: string;
        finishReason: string;
        totalTokens?: number;
        promptTokens?: number;
        completionTokens?: number;
      }
    | { type: "run.error"; code: string; message: string }
    | { type: "ping" }
    /** A side-effect event with its established v1 payload (suggestions, Missions, dashboards). */
    | { type: "aux"; event: AssistantEvent }
  );

const AUX = new Set([
  "suggestions",
  "mission_updated",
  "mission_continuation",
  "dashboard_changed",
  "dashboard_activity",
  "client_action",
]);

const PART_TYPES = new Set([
  "reasoning", "text", "tool", "guard", "hitl_request", "table", "chart", "citation", "dashboard", "ui",
]);
const HITL_KINDS = new Set(["approval", "question", "form", "choice"]);

const END_STATES = new Set(["done", "error", "cancelled"]);

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function position(data: Record<string, unknown>): { contentIndex?: number; contentId?: string } {
  return {
    ...(Number.isInteger(data.content_index) ? { contentIndex: data.content_index as number } : {}),
    ...(typeof data.content_id === "string" ? { contentId: data.content_id } : {}),
  };
}

function nested<T extends AssistantEvent["type"]>(
  name: T,
  payload: unknown,
): Extract<AssistantEvent, { type: T }> | null {
  const event = parseAssistantEvent(name, JSON.stringify(payload ?? {}));
  return event && event.type === name ? (event as Extract<AssistantEvent, { type: T }>) : null;
}

function partStart(data: Record<string, unknown>): PartStartFields | null {
  const type = data.type;
  if (typeof type !== "string" || !PART_TYPES.has(type)) return null;
  switch (type) {
    case "reasoning":
      return typeof data.text === "string"
        ? {
            type,
            source: data.source === "model" ? "model" : "nova",
            text: data.text,
            ...(typeof data.phase === "string" ? { phase: data.phase as ThinkingPhase } : {}),
            ...(typeof data.plan_steps === "number" ? { planSteps: data.plan_steps } : {}),
          }
        : null;
    case "text":
      return { type, ...position(data) };
    case "tool":
      return typeof data.tool_call_id === "string" && typeof data.tool_name === "string"
        ? {
            type,
            toolCallId: data.tool_call_id,
            toolName: data.tool_name,
            ...(typeof data.origin_run_id === "string" ? { originRunId: data.origin_run_id } : {}),
            ...(typeof data.skill_name === "string" ? { skillName: data.skill_name } : {}),
            ...(typeof data.classification === "string"
              ? { classification: data.classification as ToolCallView["classification"] }
              : {}),
          }
        : null;
    case "hitl_request":
      return typeof data.request_id === "string" && typeof data.kind === "string" && HITL_KINDS.has(data.kind)
        ? {
            type,
            requestId: data.request_id,
            kind: data.kind as HitlKind,
            ...(typeof data.tool_part_id === "string" ? { toolPartId: data.tool_part_id } : {}),
          }
        : null;
    case "guard": {
      const guard = nested("guard", data.decision);
      if (!guard) return null;
      const { type: _type, run_id: _run, sequence: _seq, ...decision } = guard;
      return { type, decision };
    }
    case "table": {
      const table = nested("table", data.block);
      return table ? { type, block: table.payload, ...position(data) } : null;
    }
    case "chart": {
      const chart = nested("chart", data.block);
      return chart ? { type, block: chart.payload, ...position(data) } : null;
    }
    case "citation": {
      const citation = nested("citation", data.block);
      return citation ? { type, block: citation.payload, ...position(data) } : null;
    }
    case "dashboard": {
      const dashboard = nested("dashboard", {
        payload: data.block,
        content_id: data.content_id ?? "",
        content_index: data.content_index ?? 0,
      });
      return dashboard ? { type, block: dashboard.payload, ...position(data) } : null;
    }
    case "ui": {
      const ui = nested("ui", {
        payload: data.block,
        content_id: data.content_id ?? "",
        content_index: data.content_index ?? 0,
      });
      return ui ? { type, block: ui.payload, ...position(data) } : null;
    }
    default:
      return null;
  }
}

/**
 * Parse one v2 SSE frame. Returns null for a frame without a valid envelope or
 * payload, so an added server event cannot crash the transcript.
 */
export function parseStudioEvent(name: string, raw: string): StudioEvent | null {
  let parsed: unknown;
  try {
    parsed = raw ? JSON.parse(raw) : {};
  } catch {
    return null;
  }
  const data = record(parsed);
  if (!data) return null;
  return parseStudioRecord(name, data);
}

/** Parse one stored journal entry or decoded frame payload. */
export function parseStudioRecord(name: string, data: Record<string, unknown>): StudioEvent | null {
  const seq = data.seq;
  if (typeof seq !== "number" || !Number.isSafeInteger(seq) || seq < 0) return null;
  const envelope: Envelope = {
    seq,
    ...(typeof data.run_id === "string" ? { run_id: data.run_id } : {}),
  };
  const partId = typeof data.part_id === "string" ? data.part_id : null;
  switch (name) {
    case "run.started":
      return {
        ...envelope,
        type: name,
        role: typeof data.role === "string" ? data.role : "",
        protocolVersion: typeof data.protocol_version === "number" ? data.protocol_version : PROTOCOL_VERSION,
      };
    case "part.start": {
      const part = partStart(data);
      return part && partId ? { ...envelope, type: name, partId, part } : null;
    }
    case "part.delta":
      return partId && typeof data.text === "string"
        ? { ...envelope, type: name, partId, text: data.text, replace: data.replace === true }
        : null;
    case "part.end":
      return partId && typeof data.state === "string" && END_STATES.has(data.state)
        ? {
            ...envelope,
            type: name,
            partId,
            state: data.state as PartEndState,
            ...(typeof data.final === "string" ? { final: data.final } : {}),
          }
        : null;
    case "tool.status": {
      const status = nested("tool_status", { tool_call_id: "x", status: data.status });
      if (!partId || !status) return null;
      const progress = record(data.progress);
      const evidence = record(data.evidence);
      return {
        ...envelope,
        type: name,
        partId,
        status: status.status,
        ...(typeof data.preview === "string" && data.preview ? { preview: data.preview } : {}),
        ...(progress && typeof progress.stage === "string" && typeof progress.text === "string"
          ? { progress: { stage: progress.stage, text: progress.text } }
          : {}),
        ...(typeof data.detail === "string" ? { detail: data.detail } : {}),
        ...(evidence ? { evidence } : {}),
      };
    }
    case "tool.evidence": {
      const kind = data.kind === "envelope" ? "evidence_envelope" : data.kind === "health" ? "evidence_health" : null;
      const event = kind ? nested(kind, data) : null;
      return partId && event ? { ...envelope, type: name, partId, event } : null;
    }
    case "input.required": {
      const request = parseHitlRequest(data.request);
      return partId && request && typeof data.request_id === "string"
        ? {
            ...envelope,
            type: name,
            partId,
            requestId: data.request_id,
            request,
            ...(typeof data.expires_at === "string" && data.expires_at ? { expiresAt: data.expires_at } : {}),
          }
        : null;
    }
    case "input.resolved": {
      const status = hitlStatus(data.status);
      const answer = record(data.answer);
      return partId && status && status !== "pending"
        ? {
            ...envelope,
            type: name,
            partId,
            status,
            summary: typeof data.summary === "string" ? data.summary : "",
            ...(answer ? { answer } : {}),
          }
        : null;
    }
    case "role.changed":
      return typeof data.active_role === "string"
        ? { ...envelope, type: name, activeRole: data.active_role }
        : null;
    case "run.finished": {
      const done = nested("done", data);
      return done
        ? {
            ...envelope,
            type: name,
            messageId: done.message_id,
            finishReason: done.finish_reason,
            ...(done.total_tokens !== undefined ? { totalTokens: done.total_tokens } : {}),
            ...(done.prompt_tokens !== undefined ? { promptTokens: done.prompt_tokens } : {}),
            ...(done.completion_tokens !== undefined ? { completionTokens: done.completion_tokens } : {}),
          }
        : null;
    }
    case "run.error":
      return typeof data.code === "string" && typeof data.message === "string"
        ? { ...envelope, type: name, code: data.code, message: data.message }
        : null;
    case "ping":
      return { ...envelope, type: "ping" };
    default: {
      if (!AUX.has(name)) return null;
      const event = parseAssistantEvent(name, JSON.stringify(data));
      return event ? { ...envelope, type: "aux", event } : null;
    }
  }
}
