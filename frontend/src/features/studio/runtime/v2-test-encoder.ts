/**
 * Test support: re-encode recorded v1 loop frames as protocol v2 events.
 *
 * Production encoding happens on the server (`StudioStreamEncoder` in
 * `backend/app/modules/assistant/events_v2.py`). Studio tests describe a turn
 * the way the loop produces it and use this mirror to obtain the v2 stream.
 * `protocol-v2-contract.json` holds one sequence encoded by the server; the
 * contract test checks this mirror against it so the two cannot drift.
 */
import type { AssistantEvent } from "@/features/assistant/types";
import { parseStudioRecord, PROTOCOL_VERSION, type StudioEvent } from "./protocol";

export type V1Frame = { event: string; data: Record<string, unknown> };
export type V2Record = { event: string; data: Record<string, unknown> };

const PASSTHROUGH = new Set([
  "suggestions",
  "mission_updated",
  "mission_continuation",
  "dashboard_changed",
  "dashboard_activity",
  "client_action",
  "execution_time_context",
  "business_result",
]);
const CONTENT_TYPES: Record<string, string> = {
  table: "table",
  chart: "chart",
  component: "component",
  citation: "citation",
  dashboard: "dashboard",
};
const TERMINAL_TOOL = new Set(["done", "failed", "denied", "cancelled"]);
const ENVELOPE = new Set(["run_id", "sequence", "seq"]);

const str = (value: unknown, fallback = "") => (value == null || value === "" ? fallback : String(value));

function body(data: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(data).filter(([key]) => !ENVELOPE.has(key)));
}

function position(data: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of ["content_index", "content_id"]) if (data[key] != null) out[key] = data[key];
  return out;
}

export class TestStudioEncoder {
  private count = 0;
  private tools = new Map<string, string>();
  private toolState = new Map<string, string>();
  // The server's test clock: each reading is 1.5 s after the previous one.
  private ticks = 0;
  private toolStarted = new Map<string, number>();
  private content = new Map<string, string>();
  private inputs = new Map<string, string>();
  private open = new Map<string, string>();
  private reasoning: [string, string, string] | null = null;
  private model: string | null = null;
  private plan: string | null = null;
  private planText = "";
  private finished = false;

  constructor(private readonly runId: string) {}

  private clock(): number {
    return this.ticks++ * 1.5;
  }

  private partId(): string {
    return `${this.runId}#${this.count++}`;
  }

  private start(type: string, fields: Record<string, unknown>): [string, V2Record] {
    const partId = this.partId();
    this.open.set(partId, type);
    return [partId, { event: "part.start", data: { part_id: partId, type, ...fields } }];
  }

  private end(partId: string, state = "done", final?: unknown): V2Record {
    this.open.delete(partId);
    return { event: "part.end", data: { part_id: partId, state, ...(final != null ? { final } : {}) } };
  }

  private closeReasoning(): V2Record[] {
    if (!this.reasoning) return [];
    const [partId] = this.reasoning;
    this.reasoning = null;
    return this.open.has(partId) ? [this.end(partId)] : [];
  }

  private toolPart(key: string, view: Record<string, unknown> = {}): [string, V2Record[]] {
    const existing = this.tools.get(key);
    if (existing) return [existing, []];
    const frames = this.closeReasoning();
    const [origin, toolCallId] = JSON.parse(key) as [string, string];
    const fields: Record<string, unknown> = { tool_call_id: toolCallId, tool_name: str(view.tool_name, "tool") };
    if (origin !== this.runId) fields.origin_run_id = origin;
    for (const name of ["skill_name", "display_name", "classification"]) if (view[name]) fields[name] = view[name];
    const [partId, frame] = this.start("tool", fields);
    this.tools.set(key, partId);
    frames.push(frame);
    return [partId, frames];
  }

  runStarted(role: string): V2Record {
    return { event: "run.started", data: { protocol_version: PROTOCOL_VERSION, role } };
  }

  translate({ event: name, data }: V1Frame): V2Record[] {
    const b = body(data);
    const origin = str(data.run_id, this.runId);
    const key = (id: unknown) => JSON.stringify([origin, str(id)]);
    if (name === "ping") return [{ event: "ping", data: {} }];
    if (PASSTHROUGH.has(name)) return [{ event: name, data: b }];
    switch (name) {
      case "thinking":
        return this.thinking(b);
      case "model_reasoning":
        return this.modelReasoning(b);
      case "narration": {
        const text = str(b.text);
        if (!text) return [];
        const frames = this.closeReasoning();
        const [partId, opened] = this.start("reasoning", { source: "narration", text: "" });
        return [...frames, opened, { event: "part.delta", data: { part_id: partId, text } }, this.end(partId)];
      }
      case "plan":
        return this.planFrames(b);
      case "tool_call":
        return this.toolCall(key(b.tool_call_id), b);
      case "tool_status":
        return b.tool_call_id ? this.toolStatus(key(b.tool_call_id), str(b.status), undefined, b.reason) : [];
      case "tool_progress":
        return this.toolProgress(key(b.tool_call_id), b);
      case "tool_detail":
        return this.toolDetail(key(b.tool_call_id), b);
      case "evidence_health":
      case "evidence_envelope":
        return this.evidence(key(b.tool_call_id), name, b);
      case "guard": {
        const frames = this.closeReasoning();
        const [partId, opened] = this.start("guard", { decision: b });
        return [...frames, opened, this.end(partId)];
      }
      case "text_delta":
        return this.text(b);
      case "ui":
        return this.ui(b);
      case "ui_delta": {
        const partId = this.content.get(str(b.content_id));
        if (!partId || !this.open.has(partId)) return [];
        return [{
          event: "part.delta",
          data: { part_id: partId, text: str(b.text), ...(b.replace === true ? { replace: true } : {}) },
        }];
      }
      case "content_block_done": {
        const partId = this.content.get(str(b.content_id));
        return partId && this.open.has(partId) ? [this.end(partId)] : [];
      }
      case "input_required":
        return this.inputRequired(key(b.request_id), origin, b);
      case "input_resolved":
        return this.inputResolved(key(b.request_id), b);
      case "role_changed":
        return this.roleChanged(b);
      case "artifact_draft": {
        const draft = b.draft;
        if (!draft || typeof draft !== "object" || typeof (draft as Record<string, unknown>).draft_ref !== "string") return [];
        return [...this.closeReasoning(), { event: "artifact.draft", data: { part_id: this.partId(), draft } }];
      }
      case "save_suggested": {
        const refs = (Array.isArray(b.draft_refs) ? b.draft_refs : []).filter((ref): ref is string => typeof ref === "string");
        if (!refs.length || (b.capability !== "can_save" && b.capability !== "role_cannot_author")) return [];
        const frames = this.closeReasoning();
        return [...frames, {
          event: "save.suggested",
          data: {
            part_id: this.partId(), draft_ref: refs[0], draft_refs: refs, capability: b.capability,
            reason_code: str(b.reason_code), message: str(b.message),
          },
        }];
      }
      case "error":
        return [{ event: "run.error", data: { code: str(b.code, "error"), message: str(b.message) } }];
      case "done":
        return this.finish(b);
      default:
        if (name in CONTENT_TYPES) return this.block(CONTENT_TYPES[name], b);
        return [];
    }
  }

  finish(done: Record<string, unknown>): V2Record[] {
    if (this.finished) return [];
    this.finished = true;
    const reason = str(done.finish_reason, "stop");
    const state = reason === "cancelled" ? "cancelled" : reason === "error" || reason === "interrupted" ? "error" : "done";
    this.reasoning = null;
    this.model = null;
    const frames: V2Record[] = [];
    const waiting = new Set(
      [...this.tools.entries()].filter(([call]) => this.toolState.get(call) === "pending").map(([, partId]) => partId),
    );
    for (const [partId, type] of [...this.open.entries()]) {
      if (type === "tool" && waiting.has(partId)) frames.push(this.end(partId, "cancelled"));
      else if (type === "hitl_request") {
        const status = reason === "consent_timeout" ? "expired" : "cancelled";
        frames.push({
          event: "input.resolved",
          data: {
            part_id: partId,
            status,
            summary: status === "expired" ? "The request expired" : "Stopped before you answered",
          },
        });
        frames.push(this.end(partId, "cancelled"));
      } else frames.push(this.end(partId, state));
    }
    const payload: Record<string, unknown> = { message_id: str(done.message_id), finish_reason: reason };
    for (const key of ["usage", "source", "outcome"]) if (done[key]) payload[key] = done[key];
    frames.push({ event: "run.finished", data: payload });
    return frames;
  }

  private modelReasoning(b: Record<string, unknown>): V2Record[] {
    const text = str(b.text);
    if (str(b.status, "running") === "done") {
      const partId = this.model;
      this.model = null;
      return partId && this.open.has(partId) ? [this.end(partId)] : [];
    }
    if (!text) return [];
    const frames: V2Record[] = [];
    if (!this.model || !this.open.has(this.model)) {
      frames.push(...this.closeReasoning());
      const [partId, opened] = this.start("reasoning", { source: "model", text: "" });
      this.model = partId;
      frames.push(opened);
    }
    frames.push({ event: "part.delta", data: { part_id: this.model, text } });
    return frames;
  }

  private thinking(b: Record<string, unknown>): V2Record[] {
    const phase = str(b.phase, "act");
    const text = str(b.text);
    const status = str(b.status, "running");
    const current = this.reasoning;
    if (current && current[1] === phase && this.open.has(current[0])) {
      if (status === "done") {
        this.reasoning = null;
        return [this.end(current[0], "done", text !== current[2] ? text : undefined)];
      }
      if (text === current[2]) return [];
      this.reasoning = [current[0], phase, text];
      return [{ event: "part.delta", data: { part_id: current[0], text, replace: true } }];
    }
    const frames = this.closeReasoning();
    const [partId, opened] = this.start("reasoning", { source: "nova", phase, text });
    frames.push(opened);
    if (status === "done") frames.push(this.end(partId));
    else this.reasoning = [partId, phase, text];
    return frames;
  }

  private planFrames(b: Record<string, unknown>): V2Record[] {
    const steps = Array.isArray(b.steps) ? b.steps : [];
    const text = steps
      .flatMap((step, index) =>
        step && typeof step === "object" ? [`${index + 1}. ${str((step as Record<string, unknown>).text)}`] : [],
      )
      .join("\n");
    if (this.plan) {
      // One plan row per run: an unchanged plan adds nothing, a changed one replaces it.
      if (text === this.planText) return [];
      this.planText = text;
      return [{ event: "part.delta", data: { part_id: this.plan, text, replace: true } }];
    }
    const frames = this.closeReasoning();
    const [partId, opened] = this.start("reasoning", { source: "nova", phase: "plan", text, plan_steps: steps.length });
    this.plan = partId;
    this.planText = text;
    return [...frames, opened, this.end(partId)];
  }

  private toolCall(toolKey: string, view: Record<string, unknown>): V2Record[] {
    if (!str(view.tool_call_id)) return [];
    const [partId, frames] = this.toolPart(toolKey, view);
    const status = str(view.status, "running");
    const preview = view.sql_preview || undefined;
    if (status === "pending") {
      this.toolState.set(toolKey, status);
      frames.push({ event: "tool.status", data: { part_id: partId, status, ...(preview ? { preview } : {}) } });
      return frames;
    }
    frames.push(...this.toolStatus(toolKey, status, preview));
    return frames;
  }

  private toolStatus(toolKey: string, status: string, preview?: unknown, reason?: unknown): V2Record[] {
    if (!status) return [];
    const [partId, frames] = this.toolPart(toolKey);
    if (!this.open.has(partId)) return frames;
    this.toolState.set(toolKey, status);
    let duration: number | undefined;
    if (status === "running") {
      if (!this.toolStarted.has(toolKey)) this.toolStarted.set(toolKey, this.clock());
    } else if (TERMINAL_TOOL.has(status) && this.toolStarted.has(toolKey)) {
      duration = Math.max(0, Math.round((this.clock() - this.toolStarted.get(toolKey)!) * 1000));
      this.toolStarted.delete(toolKey);
    }
    frames.push({
      event: "tool.status",
      data: {
        part_id: partId,
        status,
        ...(preview ? { preview } : {}),
        ...(status === "failed" && typeof reason === "string" && reason ? { reason } : {}),
        ...(duration !== undefined ? { duration_ms: duration } : {}),
      },
    });
    if (TERMINAL_TOOL.has(status))
      frames.push(this.end(partId, status === "failed" ? "error" : status === "denied" || status === "cancelled" ? "cancelled" : "done"));
    return frames;
  }

  private toolProgress(toolKey: string, b: Record<string, unknown>): V2Record[] {
    if (!str(b.tool_call_id)) return [];
    const [partId, frames] = this.toolPart(toolKey);
    if (!this.open.has(partId)) return frames;
    const stage = str(b.stage);
    frames.push({
      event: "tool.status",
      data: {
        part_id: partId,
        status: stage === "query_completed" ? "done" : "running",
        progress: { stage, text: str(b.text) },
        ...(b.sql_preview ? { preview: b.sql_preview } : {}),
      },
    });
    return frames;
  }

  private toolDetail(toolKey: string, b: Record<string, unknown>): V2Record[] {
    const partId = this.tools.get(toolKey);
    if (!partId) return [];
    const extra = Object.fromEntries(Object.entries(b).filter(([key]) => key !== "tool_call_id" && key !== "text"));
    return [
      {
        event: "tool.status",
        data: {
          part_id: partId,
          status: this.toolState.get(toolKey) ?? "done",
          detail: str(b.text),
          ...(Object.keys(extra).length ? { evidence: extra } : {}),
        },
      },
    ];
  }

  private evidence(toolKey: string, name: string, b: Record<string, unknown>): V2Record[] {
    const toolCallId = str(b.tool_call_id);
    const partId = this.tools.get(toolKey);
    if (!partId) return [];
    const rest = Object.fromEntries(Object.entries(b).filter(([key]) => key !== "tool_call_id"));
    return [
      {
        event: "tool.evidence",
        data: { part_id: partId, kind: name.replace(/^evidence_/, ""), ...rest, tool_call_id: toolCallId },
      },
    ];
  }

  private text(b: Record<string, unknown>): V2Record[] {
    const contentId = str(b.content_id, "text");
    let partId = this.content.get(contentId);
    const frames: V2Record[] = [];
    if (!partId || !this.open.has(partId)) {
      frames.push(...this.closeReasoning());
      const fields = position(b);
      if (b.verification === "verified" || b.verification === "adjusted") fields.verification = b.verification;
      const [id, opened] = this.start("text", fields);
      partId = id;
      this.content.set(contentId, partId);
      frames.push(opened);
    }
    frames.push({ event: "part.delta", data: { part_id: partId, text: str(b.text) } });
    return frames;
  }

  private block(type: string, b: Record<string, unknown>): V2Record[] {
    const contentId = str(b.content_id);
    const frames = this.closeReasoning();
    const block =
      type === "dashboard"
        ? b.payload
        : Object.fromEntries(Object.entries(b).filter(([key]) => key !== "content_index" && key !== "content_id"));
    const [partId, opened] = this.start(type, { block, ...position(b) });
    frames.push(opened);
    if (contentId) this.content.set(contentId, partId);
    else frames.push(this.end(partId));
    return frames;
  }

  private ui(b: Record<string, unknown>): V2Record[] {
    const contentId = str(b.content_id);
    if (!contentId || this.content.has(contentId)) return [];
    const frames = this.closeReasoning();
    const [partId, opened] = this.start("ui", { block: b.payload ?? {}, ...position(b) });
    this.content.set(contentId, partId);
    frames.push(opened);
    return frames;
  }

  private inputRequired(inputKey: string, origin: string, b: Record<string, unknown>): V2Record[] {
    const requestId = str(b.request_id);
    const request = b.request;
    if (!requestId || !request || typeof request !== "object" || this.inputs.has(inputKey)) return [];
    const frames = this.closeReasoning();
    const kind = str((request as Record<string, unknown>).kind);
    const fields: Record<string, unknown> = { request_id: requestId, kind };
    if (b.tool_call_id) {
      const toolPart = this.tools.get(JSON.stringify([origin, str(b.tool_call_id)]));
      if (toolPart) fields.tool_part_id = toolPart;
    }
    const [partId, opened] = this.start("hitl_request", fields);
    this.inputs.set(inputKey, partId);
    frames.push(opened);
    frames.push({
      event: "input.required",
      data: { part_id: partId, request_id: requestId, kind, request, expires_at: str(b.expires_at) },
    });
    return frames;
  }

  private inputResolved(inputKey: string, b: Record<string, unknown>): V2Record[] {
    const partId = this.inputs.get(inputKey);
    if (!partId || !this.open.has(partId)) return [];
    const status = str(b.status, "cancelled");
    const answer = b.answer && typeof b.answer === "object" && !Array.isArray(b.answer) ? { answer: b.answer } : {};
    return [
      { event: "input.resolved", data: { part_id: partId, status, summary: str(b.summary), ...answer } },
      this.end(partId, status === "cancelled" || status === "expired" ? "cancelled" : "done"),
    ];
  }

  private roleChanged(b: Record<string, unknown>): V2Record[] {
    const frames = [...this.open.keys()].map((partId) => this.end(partId, "cancelled"));
    this.reasoning = null;
    this.model = null;
    this.plan = null;
    this.tools.clear();
    this.toolState.clear();
    this.content.clear();
    this.inputs.clear();
    frames.push({ event: "role.changed", data: { active_role: str(b.active_role) } });
    return frames;
  }
}

/** Encode a v1 turn and number it as the route does (`run_id`, `seq`). */
export function encodeV1Turn(
  frames: V1Frame[],
  { runId = "run-1", role = "ANALYST" }: { runId?: string; role?: string } = {},
): V2Record[] {
  const encoder = new TestStudioEncoder(runId);
  const out = [encoder.runStarted(role), ...frames.flatMap((frame) => encoder.translate(frame))];
  return out.map((record, seq) => ({ event: record.event, data: { ...record.data, run_id: runId, seq } }));
}

/**
 * A live stream fed one v1 event at a time, numbered like the route numbers it.
 * Returns the parsed v2 events each v1 event produced.
 */
export function liveEncoder({ runId = "run-1", role = "ANALYST" }: { runId?: string; role?: string } = {}) {
  const encoder = new TestStudioEncoder(runId);
  let seq = 0;
  let started = false;
  return (event: AssistantEvent): StudioEvent[] => {
    const records = [...(started ? [] : [encoder.runStarted(role)]), ...encoder.translate(wireFrame(event))];
    started = true;
    return parsedEvents(records.map((record) => ({ event: record.event, data: { ...record.data, run_id: runId, seq: seq++ } })));
  };
}

/** Parsed events, as `consumeAgentRun` hands them to the transcript. */
export function parsedEvents(records: V2Record[]): StudioEvent[] {
  return records.flatMap((record) => {
    const event = parseStudioRecord(record.event, record.data);
    return event ? [event] : [];
  });
}

/** The wire frame a parsed v1 event came from (the inverse of `parseAssistantEvent`). */
export function wireFrame(event: AssistantEvent): V1Frame {
  const { type, ...rest } = event as AssistantEvent & Record<string, unknown>;
  const fields = rest as Record<string, unknown>;
  switch (type) {
    case "tool_call": {
      const { payload, ...envelope } = fields;
      return { event: type, data: { ...(payload as Record<string, unknown>), ...envelope } };
    }
    case "table":
    case "chart":
    case "citation": {
      const { payload, ...position } = fields;
      return { event: type, data: { ...(payload as Record<string, unknown>), ...position } };
    }
    case "component": {
      const { block, ...position } = fields;
      return { event: type, data: { ...(block as Record<string, unknown>), ...position } };
    }
    default:
      return { event: type, data: fields };
  }
}

/** A Studio turn described as v1 loop events, as the parsed v2 stream. */
export function v2Stream(
  events: AssistantEvent[],
  options?: { runId?: string; role?: string },
): StudioEvent[] {
  return parsedEvents(encodeV1Turn(events.map(wireFrame), options));
}

function parseSse(text: string): V1Frame[] {
  return text
    .split("\n\n")
    .filter((block) => block.trim())
    .map((block) => {
      let event = "";
      let data: Record<string, unknown> = {};
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data = JSON.parse(line.slice(5).trim()) as Record<string, unknown>;
      }
      return { event, data };
    });
}

/**
 * One v2 response body built from v1 SSE text, chunk by chunk. Each call
 * continues the same run: parts and `seq` carry on as the route numbers them.
 */
export function v2Sse({ runId = "run-1", role = "ANALYST" }: { runId?: string; role?: string } = {}) {
  const encoder = new TestStudioEncoder(runId);
  let seq = 0;
  let started = false;
  return (v1: string): string => {
    const records = [...(started ? [] : [encoder.runStarted(role)]), ...parseSse(v1).flatMap((frame) => encoder.translate(frame))];
    started = true;
    return records
      .map((record) => `event: ${record.event}\ndata: ${JSON.stringify({ ...record.data, run_id: runId, seq: seq++ })}\n\n`)
      .join("");
  };
}
