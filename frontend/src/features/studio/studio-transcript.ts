import type {
  ChartBlock as ChartBlockType,
  CitationBlock,
  TableBlock,
} from "@/features/assistant/types";
import { turnOutcome } from "@/features/assistant/types";
import type { AgentMessage, GuardDecision } from "@/features/agents/api";
import type { RailStep } from "./thought-turn";
import type { AnswerFeedback } from "./answer-footer";
import type { SentAttachment } from "./studio-attachments";
import {
  evidenceHealthFromTrace,
  evidenceEnvelopeFromTrace,
  workflowProvenanceFromTrace,
  evidenceIdentity,
  mergeToolEvidence,
  readEvidenceEnvelope,
  readEvidenceHealth,
  type ToolEvidence,
} from "./evidence-health";
import { parseStudioRecord, PROTOCOL_VERSION, type StudioEvent } from "./runtime/protocol";
import { MODEL_REASONING_LABEL } from "./rail-model";
import { parseComponentBlock } from "./runtime/components/block";
import {
  emptyRunState,
  isContentPart,
  sealPart,
  turnReducer,
  type ContentPart,
  type HitlPart,
  type SaveSuggestionPart,
  type TurnPart,
  type TurnRunState,
  type WorkPart,
} from "./runtime/turn-reducer";

export { MAX_UI_PROGRAM_BYTES } from "./runtime/turn-reducer";

/** Finish reasons that end a turn normally: an answer, or a question back. */
const FINISHED = new Set(["stop", "clarification"]);

export type { StudioEvent } from "./runtime/protocol";

export type OrderedContent =
  | { id: string; index: number; type: "dashboard"; block: import("../viz/types").DashboardArtifactBlock; complete: true }
  | {
      id: string;
      index: number;
      type: "ui";
      block: import("./runtime/components/block").UiBlock;
      complete: boolean;
      /** The streamed program passed the size limit; it is shown as its fallback. */
      tooLarge?: true;
    }
  | {
      id: string;
      index: number;
      type: "text";
      text: string;
      complete: boolean;
      /** Sent after its figures were checked against the turn's results. */
      verification?: import("./runtime/protocol").TextVerification;
    }
  | {
      id: string;
      index: number;
      type: "table";
      block: TableBlock;
      complete: true;
    }
  | {
      id: string;
      index: number;
      type: "chart";
      block: ChartBlockType;
      complete: true;
    }
  | {
      id: string;
      index: number;
      type: "citation";
      block: CitationBlock;
      complete: true;
    }
  | {
      id: string;
      index: number;
      /** A registry component: a native chart, KPI tiles or a map. */
      type: "component";
      block: import("./runtime/components/block").ComponentBlock;
      complete: true;
    };

/**
 * One turn in the transcript.
 *
 * A turn is the unit a reader actually thinks in: a question, the work it
 * caused, and the answer that came out. Holding the steps on the turn, rather
 * than flattening them into top-level rows, is what lets the process collapse
 * into a single summary line and stop competing with the answer for attention.
 */
export type TranscriptTurn = {
  id: string;
  question: string;
  questionCreatedAt?: string;
  attachments?: SentAttachment[];
  /** The ordered work trace: thinking frames, tool calls, context notes. */
  steps: RailStep[];
  /** Answer prose, appended as it streams. */
  answer: string;
  /** Canonical authored output. Higher indexes wait for lower text to seal. */
  content: OrderedContent[];
  blocks: {
    tables: { id: string; block: TableBlock }[];
    charts: { id: string; block: ChartBlockType }[];
    citations: CitationBlock[];
  };
  state: "streaming" | "done" | "cancelled" | "error";
  /** Why the turn stopped, when it did not stop cleanly. */
  stopReason?: string;
  /** Follow-up questions the agent's catalog can answer next. */
  suggestions?: string[];
  error?: string;
  /**
   * A Nova-initiated turn (the reconsider pass), not something the user asked.
   * It renders as a labelled continuation rather than as a user message.
   */
  origin?: "user" | "reconsider";
  /**
   * Bumped when something happens that the user must see in the rail, such as
   * their own approval decision. The rail watches it and opens.
   */
  revealKey?: number;
  /** Total tokens the turn spent, when the provider reported any. */
  tokens?: number;
  /** The model that answered, when it is known. */
  model?: string;
  messageId?: string;
  inputTokens?: number;
  outputTokens?: number;
  feedback?: AnswerFeedback;
  evidence?: ToolEvidence[];
  toolPreviews?: Record<string, string>;
  /**
   * The source of truth: ordered parts from the protocol v2 reducer (or, for
   * a protocol 1 turn, rebuilt from its stored trace). `steps`, `content`,
   * `answer`, `blocks` and the outcome fields above are a
   * projection of it, kept for readers such as Observability and the footer.
   */
  run: TurnRunState;
  /** `2` for a streamed or journaled turn; `1` for a stored trace replayed read-only. */
  protocolVersion: 1 | 2;
};

/** A new, empty turn about to stream. */
export function newTurn(
  fields: Pick<TranscriptTurn, "id" | "question"> & Partial<TranscriptTurn>,
): TranscriptTurn {
  return {
    steps: [],
    answer: "",
    content: [],
    blocks: { tables: [], charts: [], citations: [] },
    state: "streaming",
    run: emptyRunState(),
    protocolVersion: PROTOCOL_VERSION as 2,
    ...fields,
  };
}

/** One rendering segment: adjacent work rows, adjacent content, or a separator. */
export type TurnSegment =
  | { kind: "work"; id: string; parts: WorkPart[] }
  | { kind: "content"; id: string; parts: ContentPart[] }
  | { kind: "hitl"; id: string; part: HitlPart }
  | { kind: "role_change"; id: string; activeRole: string }
  | { kind: "save"; id: string; part: SaveSuggestionPart };

/** Group a turn's ordered parts into what renders in place: work, content, a request, … */
export function turnSegments(parts: TurnPart[]): TurnSegment[] {
  const segments: TurnSegment[] = [];
  for (const part of parts) {
    const last = segments[segments.length - 1];
    if (part.type === "artifact_preview") {
      // Shown in place of the chart it belongs to (by draft_ref), not on its own.
      continue;
    } else if (part.type === "save_suggestion") {
      segments.push({ kind: "save", id: part.id, part });
    } else if (part.type === "role_change") {
      segments.push({ kind: "role_change", id: part.id, activeRole: part.activeRole });
    } else if (part.type === "hitl_request") {
      // A request to the user stands on its own, where it was asked.
      segments.push({ kind: "hitl", id: part.id, part });
    } else if (isContentPart(part)) {
      if (last?.kind === "content") last.parts.push(part);
      else segments.push({ kind: "content", id: part.id, parts: [part] });
    } else if (last?.kind === "work") {
      last.parts.push(part);
    } else {
      segments.push({ kind: "work", id: part.id, parts: [part] });
    }
  }
  return segments;
}

function toolText(part: Extract<TurnPart, { type: "tool" }>): string {
  if (part.unconfirmed) return "Result not confirmed";
  const progress = part.progress[part.progress.length - 1];
  if (part.status === "running" && progress) return progress.text;
  return describeStatus(part.toolName, part.status, part.skillName, part.displayName);
}

/** The rail rows for work parts, in their order. */
export function stepsFromParts(parts: TurnPart[]): RailStep[] {
  const steps: RailStep[] = [];
  for (const part of parts) {
    switch (part.type) {
      case "legacy_step":
        steps.push(part.step);
        break;
      case "reasoning":
        if (part.source === "model" || part.source === "narration") {
          // The model's own words: its reasoning summary under one label with a
          // preview, or its narration before a tool as the row's text.
          steps.push({
            id: part.id,
            kind: "thinking",
            label: part.source,
            text: part.source === "model" ? MODEL_REASONING_LABEL : part.text,
            status: part.state === "streaming" ? "running" : "done",
            body: part.text,
          });
          break;
        }
        steps.push(
          part.phase === "plan" && part.planSteps !== undefined
            ? {
                id: part.id,
                kind: "note",
                label: "plan",
                text: `Planned ${part.planSteps} step${part.planSteps === 1 ? "" : "s"}`,
                status: "done",
                body: part.text,
              }
            : {
                id: part.id,
                kind: "thinking",
                label: part.phase ?? "act",
                text: part.text,
                status: part.state === "streaming" ? "running" : "done",
                body: part.text,
              },
        );
        break;
      case "tool":
        steps.push({
          id: part.toolCallId,
          kind: "tool",
          label: part.toolName,
          skillName: part.skillName,
          displayName: part.displayName,
          text: toolText(part),
          status: part.status,
          preview: part.toolName === "load_skill" ? undefined : part.preview,
          detail: showsToolDetail(part.toolName) ? part.detail : undefined,
          ...(part.reason ? { reason: part.reason } : {}),
          ...(part.durationMs !== undefined ? { durationMs: part.durationMs } : {}),
        });
        break;
      case "guard":
        steps.push(guardStep(steps, part.decision));
        break;
      default:
        break;
    }
  }
  return steps;
}

/** Authored content parts as ordered blocks; `index` is their position in the turn. */
export function contentFromParts(parts: TurnPart[]): OrderedContent[] {
  const content: OrderedContent[] = [];
  for (const part of parts) {
    if (!isContentPart(part)) continue;
    const id = part.contentId ?? part.id;
    const index = content.length;
    switch (part.type) {
      case "text":
        content.push({
          id,
          index,
          type: "text",
          text: part.text,
          complete: part.state !== "streaming",
          ...(part.verification ? { verification: part.verification } : {}),
        });
        break;
      case "ui":
        // An interactive block counts as complete only when its program was sealed.
        content.push({ id, index, type: "ui", block: part.block, complete: part.state === "done", ...(part.tooLarge ? { tooLarge: true as const } : {}) });
        break;
      case "table":
        content.push({ id, index, type: "table", block: part.block, complete: true });
        break;
      case "chart":
        content.push({ id, index, type: "chart", block: part.block, complete: true });
        break;
      case "component":
        content.push({ id, index, type: "component", block: part.block, complete: true });
        break;
      case "citation":
        content.push({ id, index, type: "citation", block: part.block, complete: true });
        break;
      case "dashboard":
        content.push({ id, index, type: "dashboard", block: part.block, complete: true });
        break;
    }
  }
  return content;
}

/** Recompute the projection from `run`. */
export function syncTurn(turn: TranscriptTurn): TranscriptTurn {
  const { run } = turn;
  const content = contentFromParts(run.parts);
  return {
    ...turn,
    steps: stepsFromParts(run.parts),
    content,
    answer: content.flatMap((item) => (item.type === "text" && item.text ? [item.text] : [])).join("\n\n"),
    blocks: {
      tables: content.flatMap((item) => (item.type === "table" ? [{ id: item.id, block: item.block }] : [])),
      charts: content.flatMap((item) => (item.type === "chart" ? [{ id: item.id, block: item.block }] : [])),
      citations: content.flatMap((item) => (item.type === "citation" ? [item.block] : [])),
    },
    state: run.status,
    stopReason: run.stopReason,
    error: run.error,
    messageId: run.messageId ?? turn.messageId,
    tokens: run.tokens ?? turn.tokens,
    inputTokens: run.inputTokens ?? turn.inputTokens,
    outputTokens: run.outputTokens ?? turn.outputTokens,
    suggestions: run.suggestions,
  };
}

function toolPart(turn: TranscriptTurn, partId: string) {
  const part = turn.run.parts.find((item) => item.id === partId);
  return part?.type === "tool" ? part : undefined;
}

function withPreview(turn: TranscriptTurn, toolCallId: string, runId: string | undefined, preview: string): TranscriptTurn {
  const identity = evidenceIdentity({ toolCallId, runId });
  return {
    ...turn,
    toolPreviews: { ...turn.toolPreviews, [identity]: preview },
    evidence: turn.evidence?.map((item) =>
      evidenceIdentity(item) === identity ? { ...item, sqlPreview: preview } : item,
    ),
  };
}

/** Evidence and SQL previews a tool event adds; the transcript owns their merge rules. */
function withToolEvidence(turn: TranscriptTurn, event: StudioEvent): TranscriptTurn {
  if (event.type === "role.changed") return { ...turn, evidence: [], toolPreviews: {} };
  if (event.type !== "tool.status" && event.type !== "tool.evidence") return turn;
  const tool = toolPart(turn, event.partId);
  if (!tool) return turn;
  const toolCallId = tool.toolCallId;
  // A Mission's nested run keeps its own identity for evidence and previews;
  // the turn's own calls are identified by call id alone, as in the stored trace.
  const toolRun = tool.originRunId;
  if (event.type === "tool.status") {
    let next = event.preview && tool.toolName !== "load_skill"
      ? withPreview(turn, toolCallId, toolRun, event.preview)
      : turn;
    const health = event.evidence ? evidenceHealthFromTrace(event.evidence) : null;
    if (health)
      next = {
        ...next,
        evidence: mergeToolEvidence(next.evidence ?? [], {
          toolCallId,
          toolName: tool.toolName,
          health,
          runId: toolRun,
        }),
      };
    return next;
  }
  const source = event.event;
  const envelope = source.type === "evidence_envelope" ? readEvidenceEnvelope(source.payload) : null;
  const health = envelope?.health ?? (source.type === "evidence_health" ? readEvidenceHealth(source.payload) : null);
  if (!health) return turn;
  const workflowProvenance = workflowProvenanceFromTrace(source) ?? undefined;
  if ((source.workflow ?? source.workflow_provenance) != null && !workflowProvenance) return turn;
  const runId = workflowProvenance?.run_id ?? toolRun;
  const preview =
    turn.toolPreviews?.[evidenceIdentity({ toolCallId, runId })] ?? (!workflowProvenance ? tool.preview : undefined);
  return {
    ...turn,
    evidence: mergeToolEvidence(turn.evidence ?? [], {
      toolCallId,
      toolName: source.tool_name ?? tool.toolName,
      health,
      ...(envelope ? { envelope } : {}),
      ...(workflowProvenance ? { workflowProvenance } : {}),
      runId,
      ...(preview ? { sqlPreview: preview } : {}),
    }),
  };
}

/** Fold one v2 event into a single turn (live and replay share this). */
export function applyToTurn(turn: TranscriptTurn, event: StudioEvent): TranscriptTurn {
  const run = turnReducer(turn.run, event);
  if (run === turn.run) return turn;
  return syncTurn(withToolEvidence({ ...turn, run, protocolVersion: 2 }, event));
}

/**
 * Fold one v2 event into the transcript.
 *
 * A `role.changed` keeps only this turn: earlier turns belong to the previous
 * role and stay hidden. The turn shows a separator that says so.
 */
export function applyStudioEvent(
  turns: TranscriptTurn[],
  turnId: string,
  event: StudioEvent,
): TranscriptTurn[] {
  const scoped = event.type === "role.changed" ? turns.filter((turn) => turn.id === turnId) : turns;
  return patchTurn(scoped, turnId, (turn) => applyToTurn(turn, event));
}

/** End a turn locally (transport failure, stop, or a stream that closed early). */
export function settleTurn(
  turn: TranscriptTurn,
  status: TranscriptTurn["state"],
  error?: string,
): TranscriptTurn {
  if (turn.run.status !== "streaming" && status === "done") return turn;
  const reason = status === "error" ? "error" : status === "cancelled" ? "cancelled" : "stop";
  const parts = turn.run.parts.map((part) => (part.state === "streaming" ? sealPart(part, reason) : part));
  return syncTurn({
    ...turn,
    run: { ...turn.run, parts, status, ...(error !== undefined ? { error } : {}) },
  });
}

type JournalStep = Extract<NonNullable<AgentMessage["steps"]>[number], { kind: "event_journal" }>;

function journalOf(message: AgentMessage): JournalStep | undefined {
  return message.steps?.find(
    (step): step is JournalStep => step.kind === "event_journal" && step.protocol_version === PROTOCOL_VERSION,
  );
}

/** Replay a protocol v2 turn: the stored journal, then its stored outcome. */
function replayJournal(turn: TranscriptTurn, journal: JournalStep, message: AgentMessage): TranscriptTurn {
  let replayed: TranscriptTurn = { ...turn, run: emptyRunState(), protocolVersion: 2, evidence: [], toolPreviews: {} };
  for (const entry of journal.events) {
    const event = parseStudioRecord(entry.event, entry.data);
    if (event) replayed = applyToTurn(replayed, event);
  }
  const outcome = message.steps?.find((step) => step.kind === "turn_outcome");
  const storedOutcome = outcome && "outcome" in outcome ? turnOutcome(outcome.outcome) : undefined;
  // The terminal event follows the message write; it is rebuilt from the row.
  return applyToTurn(replayed, {
    type: "run.finished",
    seq: replayed.run.lastSeq + 1,
    ...(replayed.run.runId ? { run_id: replayed.run.runId } : {}),
    messageId: message.message_id,
    finishReason: outcome && "finish_reason" in outcome && outcome.finish_reason ? outcome.finish_reason : "stop",
    ...(message.total_tokens != null ? { totalTokens: message.total_tokens } : {}),
    ...(message.prompt_tokens != null ? { promptTokens: message.prompt_tokens } : {}),
    ...(message.completion_tokens != null ? { completionTokens: message.completion_tokens } : {}),
    ...(storedOutcome ? { outcome: storedOutcome } : {}),
  });
}

/** Parts for a protocol 1 turn: its rebuilt rows, then its ordered content. */
function legacyRun(turn: TranscriptTurn): TurnRunState {
  const parts: TurnPart[] = turn.steps.map((step, seq) => ({
    id: `${turn.id}:step:${step.id}`,
    seq,
    state: "done" as const,
    type: "legacy_step" as const,
    step,
  }));
  const ordered = [...turn.content].sort((a, b) => a.index - b.index);
  ordered.forEach((item, offset) => {
    const base = {
      id: `${turn.id}:content:${item.id}`,
      seq: parts.length + offset,
      contentIndex: item.index,
      contentId: item.id,
    };
    const state = item.complete ? ("done" as const) : ("error" as const);
    switch (item.type) {
      case "text":
        parts.push({ ...base, state, type: "text", text: item.text });
        break;
      case "ui":
        parts.push({ ...base, state, type: "ui", block: item.block });
        break;
      case "table":
        parts.push({ ...base, state, type: "table", block: item.block });
        break;
      case "chart":
        parts.push({ ...base, state, type: "chart", block: item.block });
        break;
      case "component":
        parts.push({ ...base, state, type: "component", block: item.block });
        break;
      case "citation":
        parts.push({ ...base, state, type: "citation", block: item.block });
        break;
      case "dashboard":
        parts.push({ ...base, state, type: "dashboard", block: item.block });
        break;
    }
  });
  return {
    parts,
    lastSeq: parts.length - 1,
    status: turn.state,
    stopReason: turn.stopReason,
    messageId: turn.messageId,
    tokens: turn.tokens,
    inputTokens: turn.inputTokens,
    outputTokens: turn.outputTokens,
  };
}

/**
 * Whether a tool's recorded summary is written for the reader.
 *
 * `load_skill` carries the skill body, and `semantic_query` /
 * `semantic_search` carry the bounded readback the model consumes (question,
 * statement, rows). The rail already discloses those statements and results,
 * so the payload would only repeat them under the step.
 */
function showsToolDetail(name: string | null | undefined): boolean {
  return (
    name !== "load_skill" &&
    name !== "semantic_query" &&
    name !== "semantic_search"
  );
}

/**
 * Rebuild a transcript from a persisted thread.
 *
 * The live transcript is built from SSE frames; this rebuilds the same shape
 * from the trace the loop recorded, so a reopened conversation shows the work
 * that produced each answer instead of the answer alone. A user message opens a
 * turn; the assistant message that follows fills it.
 *
 * Anything the stored trace cannot express is simply absent, never invented: a
 * turn recorded before a step kind existed renders with fewer rows, not with
 * placeholder ones.
 */
export function replayThread(messages: AgentMessage[]): TranscriptTurn[] {
  const turns: TranscriptTurn[] = [];
  let open: TranscriptTurn | null = null;

  for (const message of messages) {
    if (message.role === "user") {
      open = {
        id: message.message_id,
        question: message.content,
        questionCreatedAt: message.created_at,
        attachments: message.attachments?.map((item) => ({
          name: item.name,
          sizeBytes: item.size_bytes,
          mediaType: item.media_type ?? "text/plain",
        })),
        steps: [],
        answer: "",
        content: [],
        blocks: { tables: [], charts: [], citations: [] },
        state: "done",
        run: emptyRunState("done"),
        protocolVersion: 1,
      };
      turns.push(open);
      continue;
    }
    if (message.role !== "assistant") continue;
    if (!open) {
      // A stored assistant turn with no preceding question: a legacy or
      // truncated thread. It still opens as its own turn.
      open = {
        id: message.message_id,
        question: "",
        steps: [],
        answer: "",
        content: [],
        blocks: { tables: [], charts: [], citations: [] },
        state: "done",
        run: emptyRunState("done"),
        protocolVersion: 1,
      };
      turns.push(open);
    }
    open.answer = message.content;
    open.state = "done";
    open.tokens = message.total_tokens ?? undefined;
    open.messageId = message.message_id;
    open.feedback = message.feedback ?? null;
    open.inputTokens = message.prompt_tokens ?? undefined;
    open.outputTokens = message.completion_tokens ?? undefined;
    open.model = message.model_name ?? undefined;
    let restoredText = false;

    for (const step of message.steps ?? []) {
      switch (step.kind) {
        case "turn_outcome":
          // A question back to the user is a finished answer, not a failed run.
          open.stopReason = FINISHED.has(step.finish_reason) ? undefined : step.finish_reason;
          if (step.finish_reason === "cancelled") open.state = "cancelled";
          else if (step.finish_reason && !FINISHED.has(step.finish_reason))
            open.state = "error";
          break;
        case "dashboard":
          open.content.push({
            id: step.content_id,
            index: step.content_index,
            type: "dashboard",
            block: {
              artifact_id: step.artifact_id,
              title: step.title,
              charts: step.charts,
              fallback: step.fallback,
            },
            complete: true,
          });
          break;
        case "ui":
          open.content.push({
            id: step.content_id,
            index: step.content_index,
            type: "ui",
            block: step,
            complete: true,
          });
          break;
        case "guard":
          open.steps.push(guardStep(open.steps, step));
          break;
        case "reasoning":
          open.steps.push({
            id: uniqueStepId(open.steps, `think-${step.phase}`),
            kind: "thinking",
            label: step.phase,
            text: step.text,
            status: "done",
            body: step.text,
          });
          break;
        case "tool": {
          const envelope = evidenceEnvelopeFromTrace(step);
          const health = envelope?.health ?? evidenceHealthFromTrace(step);
          const workflowProvenance =
            workflowProvenanceFromTrace(step) ?? undefined;
          if (health)
            open.evidence = mergeToolEvidence(open.evidence ?? [], {
              toolCallId:
                step.tool_call_id || `${step.name}-${open.steps.length}`,
              toolName: step.name,
              health,
              envelope: envelope ?? undefined,
              workflowProvenance,
              runId: workflowProvenance?.run_id ?? step.run_id,
              sqlPreview: step.preview || undefined,
            });
          open.steps.push({
            id: step.tool_call_id || `${step.name}-${open.steps.length}`,
            kind: "tool",
            label: step.name,
            skillName:
              step.name === "load_skill" ? step.arguments?.name : undefined,
            text:
              step.name === "load_skill"
                ? describeStatus(step.name, step.status, step.arguments?.name)
                : step.status_text || describeTool(step.name),
            status: step.status === "failed" ? "failed" : "done",
            preview:
              step.name === "load_skill"
                ? undefined
                : step.preview || undefined,
            detail: showsToolDetail(step.name)
              ? step.detail || undefined
              : undefined,
          });
          break;
        }
        case "text": {
          const index = step.content_index;
          open.content.push({
            id: step.content_id || `${open.id}-text-${index}`,
            index,
            type: "text",
            text: step.text,
            complete: true,
          });
          restoredText = true;
          break;
        }
        case "table":
          {
            const id =
              step.content_id ||
              `${open.id}-table-${open.blocks.tables.length}`;
            const block = {
              tool_call_id: step.tool_call_id,
              semantic_provenance_ref: step.semantic_provenance_ref,
              title: step.title,
              columns: step.columns,
              rows: step.rows,
              truncated: step.truncated === true || undefined,
            };
            open.blocks.tables.push({ id, block });
            open.content.push({
              id,
              index:
                step.content_index ?? nextLegacyArtifactIndex(open.content),
              type: "table",
              block,
              complete: true,
            });
          }
          break;
        case "chart":
          {
            const id =
              step.content_id ||
              `${open.id}-chart-${open.blocks.charts.length}`;
            const block = {
              tool_call_id: step.tool_call_id,
              chart_spec: step.chart_spec,
              viz_spec: step.viz_spec ?? null,
              presmodel: step.presmodel ?? null,
              ...(step.source_tool_call_id
                ? { source_tool_call_id: step.source_tool_call_id }
                : {}),
            };
            open.blocks.charts.push({ id, block });
            open.content.push({
              id,
              index:
                step.content_index ?? nextLegacyArtifactIndex(open.content),
              type: "chart",
              block,
              complete: true,
            });
          }
          break;
        case "component":
          {
            // A component the registry no longer draws is left out, not guessed.
            const block = parseComponentBlock(step);
            if (block)
              open.content.push({
                id: step.content_id,
                index: step.content_index,
                type: "component",
                block,
                complete: true,
              });
          }
          break;
        case "citation":
          open.blocks.citations.push(...step.citations);
          for (const citation of step.citations) {
            open.content.push({
              id:
                step.content_id || `${open.id}-citation-${open.content.length}`,
              index:
                step.content_index ?? nextLegacyArtifactIndex(open.content),
              type: "citation",
              block: citation,
              complete: true,
            });
          }
          break;
        default:
          // `answer` and `context` carry no row of their own.
          break;
      }
    }
    if (!restoredText && message.content) {
      open.content.push({
        id: `${open.id}-text-0`,
        index: 0,
        type: "text",
        text: message.content,
        complete: true,
      });
    }
    const journal = journalOf(message);
    if (journal) {
      // Evidence comes from the recorded tool trace, as for every stored turn.
      const evidence = open.evidence;
      const replayed = replayJournal(open, journal, message);
      Object.assign(open, replayed, evidence?.length ? { evidence } : {});
    } else {
      open.run = legacyRun(open);
      open.protocolVersion = 1;
    }
  }

  return turns;
}

export function patchTurn(
  turns: TranscriptTurn[],
  turnId: string,
  patch: (turn: TranscriptTurn) => TranscriptTurn,
): TranscriptTurn[] {
  return turns.map((turn) => (turn.id === turnId ? patch(turn) : turn));
}

/**
 * Close a turn whose stream has ended.
 *
 * A turn the server closed (its terminal event set the message id) keeps the
 * outcome that event gave it. A stopped turn whose stream ended without it,
 * because the browser stopped listening first, settles exactly as the server's
 * cancelled terminal would have settled it.
 */
export function settleStream(
  turns: TranscriptTurn[],
  turnId: string,
  stopped: boolean,
): TranscriptTurn[] {
  return patchTurn(turns, turnId, (turn) => {
    if (turn.run.status !== "streaming") return turn;
    return settleTurn(turn, stopped && turn.messageId === undefined ? "cancelled" : "done");
  });
}

/**
 * A rail-step id that no step in `steps` already holds.
 *
 * Two rows must never share a key: React would treat them as one and drop or
 * duplicate a row, and a settled `act` followed by a later `act` is a real
 * sequence, not a repeat. The base id is used when free, so the common single
 * occurrence keeps its stable, readable name; a suffix is appended only on a
 * collision.
 */
function uniqueStepId(steps: RailStep[], base: string): string {
  const taken = new Set(steps.map((step) => step.id));
  if (!taken.has(base)) return base;
  let suffix = steps.length;
  let candidate = `${base}-${suffix}`;
  while (taken.has(candidate)) {
    suffix += 1;
    candidate = `${base}-${suffix}`;
  }
  return candidate;
}

const GUARD_VERB: Record<GuardDecision["action"], string> = {
  blocked: "Guard blocked a call",
  hinted: "Guard added a hint",
  flagged: "Guard flagged instructions inside the data",
  redacted: "Guard removed a credential from the answer",
};

/** A rail row for one harness guard decision, live or replayed. */
function guardStep(steps: RailStep[], decision: GuardDecision): RailStep {
  const where = (decision.locations ?? [])
    .map((item) =>
      item.column !== undefined
        ? `row ${(item.row ?? 0) + 1}, ${item.column}`
        : (item.field ?? ""),
    )
    .filter(Boolean)
    .join("; ");
  return {
    id: uniqueStepId(steps, `guard-${decision.rule_id}`),
    kind: "note",
    label: "guard",
    text: GUARD_VERB[decision.action] ?? "Guard decision",
    status: decision.action === "blocked" ? "denied" : "done",
    body: [decision.message, where ? `Where: ${where}` : "", `Rule: ${decision.rule_id}`]
      .filter(Boolean)
      .join("\n"),
  };
}

function nextLegacyArtifactIndex(content: OrderedContent[]): number {
  const highestArtifact = content.reduce(
    (highest, item) =>
      item.type === "text" ? highest : Math.max(highest, item.index),
    0,
  );
  return highestArtifact + 1;
}

/** A tool name turned into the line a reader understands. */
export function describeTool(
  name: string,
  skillName?: string | null,
  displayName?: string | null,
): string {
  // A connector tool's identifier is opaque; its plain label names it instead.
  if (displayName && !KNOWN_TOOLS.has(name)) return `Used ${displayName}`;
  switch (name) {
    case "query_execute":
      return "Ran a SQL query";
    case "load_skill":
      return skillName ? `Loaded a skill: ${skillName}` : "Loaded a skill";
    case "semantic_query":
      return "Queried the semantic model";
    case "semantic_search":
      return "Searched the semantic model";
    case "data_to_chart":
      return "Built a chart";
    case "compute_metrics":
      return "Calculated from the results";
    case "native_dashboard_list":
      return "Listed your dashboards";
    case "native_dashboard_read":
      return "Read the dashboard";
    case "native_dataset_describe":
      return "Read the dataset's fields";
    case "native_dataset_query":
    case "dataviz_query":
      return "Queried the dashboard data";
    case "native_dashboard_add_charts":
      return "Added charts to the dashboard";
    case "native_chart_update":
      return "Updated a chart";
    case "native_dashboard_arrange":
      return "Rearranged the dashboard";
    case "native_dashboard_filters":
      return "Updated the dashboard filters";
    case "native_dashboard_create":
      return "Created a dashboard";
    case "native_dashboard_attach_dataset":
      return "Added a dataset to the dashboard";
    case "native_dashboard_publish":
      return "Published the dashboard";
    default: {
      const known = AGENT_TOOLS[name];
      if (known) return known[0];
      // Never show an internal identifier: "inspect_role_access" → "Inspect role access".
      return name ? `${name.replace(/^native_/, "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())}` : "Used a tool";
    }
  }
}

/** Agent tools, as [done, in progress], in the words the rail and approval card use. */
const AGENT_TOOLS: Record<string, [string, string]> = {
  semantic_view_query: ["Queried the semantic view", "Querying the semantic view"],
  diagnose_change: ["Diagnosed the change", "Diagnosing the change"],
  decision_lab: ["Analyzed drivers and trends", "Analyzing drivers and trends"],
  context_graph: ["Looked up the business context", "Looking up the business context"],
  analysis_workspace: ["Ran an analysis", "Running an analysis"],
  analyze_documents: ["Counted document themes", "Counting document themes"],
  execute_business_action: ["Ran a business action", "Running a business action"],
  incident_context: ["Read the incident", "Reading the incident"],
  investigation_update: ["Posted an investigation finding", "Posting an investigation finding"],
  schedule_automation: ["Scheduled a question", "Scheduling a question"],
  ai_search: ["Searched the documents", "Searching the documents"],
  catalog_search: ["Searched the data catalog", "Searching the data catalog"],
  web_search: ["Searched the web", "Searching the web"],
  feature_lookup: ["Looked up features", "Looking up features"],
  ml_execute: ["Ran a model", "Running a model"],
  ontology_resolve: ["Checked the business glossary", "Checking the business glossary"],
  ontology_explain: ["Checked the business glossary", "Checking the business glossary"],
  knowledge_propose: ["Proposed a glossary change", "Proposing a glossary change"],
};

const ACTIVITY: Record<string, string> = {
  query_execute: "Running a SQL query",
  semantic_query: "Querying the semantic model",
  semantic_search: "Searching the semantic model",
  data_to_chart: "Building a chart",
  compute_metrics: "Calculating from the results",
  native_dashboard_list: "Listing your dashboards",
  native_dashboard_read: "Reading the dashboard",
  native_dataset_describe: "Reading the dataset's fields",
  native_dataset_query: "Querying the dashboard data",
  dataviz_query: "Querying the dashboard data",
  native_dashboard_add_charts: "Adding charts to the dashboard",
  native_chart_update: "Updating a chart",
  native_dashboard_arrange: "Rearranging the dashboard",
  native_dashboard_filters: "Updating the dashboard filters",
  native_dashboard_create: "Creating a dashboard",
  native_dashboard_attach_dataset: "Adding a dataset to the dashboard",
  native_dashboard_publish: "Publishing the dashboard",
};

const KNOWN_TOOLS = new Set(["load_skill", "semantic_search", ...Object.keys(ACTIVITY)]);

/** What a step is doing, in present tense, without the tool's identifier. */
function describeActivity(
  name: string,
  skillName?: string | null,
  displayName?: string | null,
): string {
  if (name === "load_skill") return skillName ? `Loading skill: ${skillName}` : "Loading a skill";
  if (displayName && !KNOWN_TOOLS.has(name) && !AGENT_TOOLS[name]) return `Using ${displayName}`;
  const known = ACTIVITY[name] ?? AGENT_TOOLS[name]?.[1];
  if (known) return known;
  const words = name.replace(/^native_/, "").replace(/_/g, " ").trim();
  return words ? `Running: ${words}` : "Using a tool";
}

function describeStatus(
  name: string,
  status: string,
  skillName?: string | null,
  displayName?: string | null,
): string {
  // Status lines never show the tool's identifier to the reader.
  const activity = describeActivity(name, skillName, displayName);
  const lower = activity.charAt(0).toLowerCase() + activity.slice(1);
  switch (status) {
    case "running":
    case "pending":
      // Waiting for approval is not done yet.
      return activity;
    case "done":
      return describeTool(name, skillName, displayName);
    case "failed":
      return `Stopped: ${lower}`;
    case "denied":
      return `Not approved: ${lower}`;
    case "cancelled":
      return `Cancelled: ${lower}`;
    default:
      return describeTool(name, skillName, displayName);
  }
}
