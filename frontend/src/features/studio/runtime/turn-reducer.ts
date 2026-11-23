/**
 * One Studio turn as an ordered list of typed parts.
 *
 * Live streaming and replay of a stored turn run the same reducer over the
 * same events (protocol v2), so a reopened conversation shows exactly the
 * order it streamed in. Parts are kept sorted by the `seq` of the event that
 * opened them; their ids come from the server, never from a client counter.
 *
 * Events at or below the turn's `lastSeq` are ignored, which makes a resumed
 * stream (replayed from any earlier cursor) idempotent.
 */
import type {
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
import type { RailStep } from "../thought-turn";
import type { HitlKind, HitlRequestSpec, HitlStatus } from "./hitl/types";
import type { ComponentBlock } from "./components/block";
import type { PartEndState, ReasoningSource, StudioEvent, TextVerification } from "./protocol";
import type { ArtifactDraft, ArtifactSaved, SaveCapability } from "./artifacts/types";

export type PartState = "streaming" | PartEndState;

type PartBase = {
  /** Stable across live and replay: assigned by the server. */
  id: string;
  /** Position in the turn: the `seq` of the event that opened the part. */
  seq: number;
  state: PartState;
};

export type ReasoningPart = PartBase & {
  type: "reasoning";
  /** `nova` = Nova's own phase label; `model` = provider reasoning summary; `narration` = the model's words before a tool. */
  source: ReasoningSource;
  phase?: ThinkingPhase;
  text: string;
  planSteps?: number;
};

export type TextPart = PartBase & {
  type: "text";
  text: string;
  contentIndex?: number;
  contentId?: string;
  /** Set when the answer was sent after its figures were checked. */
  verification?: TextVerification;
};

export type ToolPart = PartBase & {
  type: "tool";
  toolCallId: string;
  toolName: string;
  /** The Mission run that made the call, when it is not this run. */
  originRunId?: string;
  skillName?: string;
  classification?: ToolCallView["classification"];
  status: ToolCallStatus;
  preview?: string;
  progress: { stage: string; text: string }[];
  detail?: string;
  /** The run broke off while the call ran: its result is not confirmed. */
  unconfirmed?: boolean;
};

export type GuardPart = PartBase & { type: "guard"; decision: GuardDecision };

/**
 * The turn asking its user: an approval, a question, a form or a choice. It
 * sits where it was asked and keeps how it settled, so a replay shows the
 * same answer.
 */
export type HitlPart = PartBase & {
  type: "hitl_request";
  requestId: string;
  kind: HitlKind;
  toolPartId?: string;
  /** Set by `input.required`; absent only for a part whose request was malformed. */
  request?: HitlRequestSpec;
  status: HitlStatus;
  expiresAt?: string;
  summary?: string;
  answer?: Record<string, unknown>;
};

type Positioned = { contentIndex?: number; contentId?: string };
export type TablePart = PartBase & Positioned & { type: "table"; block: TableBlock & { tool_call_id?: string } };
export type ChartPart = PartBase & Positioned & { type: "chart"; block: ChartBlock };
export type CitationPart = PartBase & Positioned & { type: "citation"; block: CitationBlock };
/** A registry component (native chart, KPI tiles, map) the answer is composed of. */
export type ComponentPart = PartBase & Positioned & { type: "component"; block: ComponentBlock };
export type DashboardPart = PartBase & Positioned & { type: "dashboard"; block: DashboardArtifactBlock };
export type UiPart = PartBase & Positioned & {
  type: "ui";
  block: UiBlock;
  /** The streamed program passed the size limit; it is shown as its fallback. */
  tooLarge?: true;
};

/**
 * The largest interactive program the browser accepts, in UTF-8 bytes. The
 * server refuses larger programs before sending them (`MAX_PROGRAM_BYTES` in
 * the assistant's intelligent UI module); this guards the stream itself, so an
 * oversized program is reported instead of being cut into a broken one.
 */
export const MAX_UI_PROGRAM_BYTES = 64_000;
const utf8 = new TextEncoder();

/**
 * A chart draft of this turn (`artifact.draft`). It renders in place of the
 * chart component it belongs to (same `draft_ref`) and opens in the artifact
 * panel; an edit there replaces `draft` with the next revision.
 */
export type ArtifactPart = PartBase & { type: "artifact_preview"; draft: ArtifactDraft; saved?: ArtifactSaved };

/** The turn's one suggestion to save its drafts, or the reason the role cannot. */
export type SaveSuggestionPart = PartBase & {
  type: "save_suggestion";
  draftRefs: string[];
  capability: SaveCapability;
  reasonCode: string;
  message: string;
  /** Drafts already saved, by `draft_ref`. */
  saved: Record<string, ArtifactSaved>;
};

/** A separator: the role changed mid-turn and earlier work was withdrawn. */
export type RoleChangePart = PartBase & { type: "role_change"; activeRole: string };

/**
 * A recorded work row of a protocol 1 turn, replayed read-only. Those turns
 * stored a trace, not events, so the row is kept as it was rebuilt.
 */
export type LegacyStepPart = PartBase & { type: "legacy_step"; step: RailStep };

export type ContentPart =
  | TextPart | TablePart | ChartPart | ComponentPart | CitationPart | DashboardPart | UiPart;
export type WorkPart = ReasoningPart | ToolPart | GuardPart | LegacyStepPart;
export type TurnPart = ContentPart | WorkPart | RoleChangePart | HitlPart | ArtifactPart | SaveSuggestionPart;

export type TurnRunState = {
  parts: TurnPart[];
  /** Highest `seq` applied; -1 before any event. */
  lastSeq: number;
  runId?: string;
  status: "streaming" | "done" | "cancelled" | "error";
  stopReason?: string;
  error?: string;
  messageId?: string;
  tokens?: number;
  inputTokens?: number;
  outputTokens?: number;
  suggestions?: string[];
  /** Set once a `role.changed` arrived in this turn. */
  roleChangedTo?: string;
};

/** Finish reasons that end a turn normally: an answer, or a question back. */
const FINISHED = new Set(["stop", "clarification"]);
const CONTENT_TYPES = new Set(["text", "table", "chart", "component", "citation", "dashboard", "ui"]);
const TERMINAL_TOOL = new Set<ToolCallStatus>(["done", "failed", "denied", "cancelled"]);

export function isContentPart(part: TurnPart): part is ContentPart {
  return CONTENT_TYPES.has(part.type);
}

export function emptyRunState(status: TurnRunState["status"] = "streaming"): TurnRunState {
  return { parts: [], lastSeq: -1, status };
}

function patchPart(state: TurnRunState, id: string, patch: (part: TurnPart) => TurnPart): TurnRunState {
  const index = state.parts.findIndex((part) => part.id === id);
  if (index === -1) return state;
  const parts = state.parts.slice();
  parts[index] = patch(parts[index]);
  return { ...state, parts };
}

function insertBySeq(parts: TurnPart[], part: TurnPart): TurnPart[] {
  // Events arrive in seq order, so this is almost always an append.
  let index = parts.length;
  while (index > 0 && parts[index - 1].seq > part.seq) index -= 1;
  return [...parts.slice(0, index), part, ...parts.slice(index)];
}

function isPlan(part: TurnPart): boolean {
  return part.type === "reasoning" && part.phase === "plan" && part.planSteps !== undefined;
}

/** The steps of a numbered plan ("1. …" per line), as its row counts them. */
function planStepCount(text: string): number {
  return text.split("\n").filter((line) => /^\d+\.\s/.test(line)).length;
}

function openPart(event: Extract<StudioEvent, { type: "part.start" }>): TurnPart {
  const base = { id: event.partId, seq: event.seq, state: "streaming" as const };
  const fields = event.part;
  switch (fields.type) {
    case "reasoning":
      return { ...base, ...fields };
    case "text":
      return { ...base, ...fields, text: "" };
    case "tool":
      return { ...base, ...fields, status: "running", progress: [] };
    case "guard":
      return { ...base, ...fields };
    case "hitl_request":
      return { ...base, ...fields, status: "pending" };
    case "ui":
      return { ...base, ...fields, block: { ...fields.block, program: "" } as UiBlock };
    default:
      return { ...base, ...fields };
  }
}

/**
 * How `run.finished` seals parts the stream left open. It mirrors the server's
 * own sealing so a replay that ends at the stored journal matches the live turn.
 */
export function sealState(finishReason: string, part: TurnPart): PartEndState {
  const state: PartEndState =
    finishReason === "cancelled" ? "cancelled" : finishReason === "error" || finishReason === "interrupted" ? "error" : "done";
  // A call still awaiting consent never ran. A run that finished normally made
  // every block it opened final, including an interactive program whose own
  // close frame was lost; the renderer still validates the program.
  if (part.type === "tool" && part.status === "pending") return "cancelled";
  if (part.type === "hitl_request") return "cancelled";
  return state;
}

/** A request the run left waiting: nobody can answer it any more. */
function sealedHitl(part: HitlPart, finishReason: string): Partial<HitlPart> {
  if (part.status !== "pending") return {};
  return finishReason === "consent_timeout"
    ? { status: "expired", summary: "The request expired" }
    : { status: "cancelled", summary: "Stopped before you answered" };
}

/** The tool status a sealed part ends with; `unconfirmed` when its outcome is unknown. */
function sealedTool(part: ToolPart, state: PartEndState): Partial<ToolPart> {
  if (TERMINAL_TOOL.has(part.status)) return {};
  if (state === "done") return { status: "done" };
  if (state === "cancelled") return { status: "cancelled" };
  // Running when the run broke off: the call may or may not have taken effect.
  return { status: "failed", unconfirmed: part.status === "running" };
}

/** Seal one part the stream left open, as `run.finished` (or a local stop) does. */
export function sealPart(part: TurnPart, finishReason: string): TurnPart {
  const state = sealState(finishReason, part);
  return {
    ...part,
    state,
    ...(part.type === "tool" ? sealedTool(part, state) : {}),
    ...(part.type === "hitl_request" ? sealedHitl(part, finishReason) : {}),
  } as TurnPart;
}

/** Apply one v2 event. Pure; returns the same object when nothing changed. */
export function turnReducer(state: TurnRunState, event: StudioEvent): TurnRunState {
  if (event.seq <= state.lastSeq) return state;
  const next = reduce(state, event);
  return { ...next, lastSeq: event.seq, ...(event.run_id && !next.runId ? { runId: event.run_id } : {}) };
}

function reduce(state: TurnRunState, event: StudioEvent): TurnRunState {
  switch (event.type) {
    case "run.started":
      return { ...state, status: "streaming" };
    case "part.start": {
      if (state.parts.some((part) => part.id === event.partId)) return state;
      const opened = openPart(event);
      // One plan row per turn: a later plan takes the earlier one's place.
      const earlier = isPlan(opened) ? state.parts.findIndex(isPlan) : -1;
      if (earlier !== -1) {
        const parts = state.parts.slice();
        parts[earlier] = { ...opened, seq: parts[earlier].seq };
        return { ...state, parts };
      }
      return { ...state, parts: insertBySeq(state.parts, opened) };
    }
    case "part.delta":
      return patchPart(state, event.partId, (part) => {
        if (part.state !== "streaming" && !event.replace) return part;
        if (isPlan(part) && event.replace) return { ...part, text: event.text, planSteps: planStepCount(event.text) };
        if (part.type === "text" || part.type === "reasoning")
          return { ...part, text: event.replace ? event.text : part.text + event.text };
        if (part.type === "ui") {
          if (part.tooLarge) return part;
          const program = event.replace ? event.text : part.block.program + event.text;
          return utf8.encode(program).length > MAX_UI_PROGRAM_BYTES
            ? { ...part, tooLarge: true as const, block: { ...part.block, program: "" } }
            : { ...part, block: { ...part.block, program } };
        }
        return part;
      });
    case "part.end":
      return patchPart(state, event.partId, (part) => ({
        ...part,
        state: event.state,
        ...(event.final !== undefined && (part.type === "text" || part.type === "reasoning")
          ? { text: event.final }
          : {}),
        ...(part.type === "tool" ? sealedTool(part, event.state) : {}),
        ...(part.type === "hitl_request" ? sealedHitl(part, "cancelled") : {}),
      }) as TurnPart);
    case "tool.status":
      return patchPart(state, event.partId, (part) =>
        part.type !== "tool"
          ? part
          : {
              ...part,
              // A detail can follow the terminal status; it never reopens the call.
              status: TERMINAL_TOOL.has(part.status) && !TERMINAL_TOOL.has(event.status) ? part.status : event.status,
              ...(event.preview ? { preview: event.preview } : {}),
              ...(event.progress ? { progress: [...part.progress, event.progress] } : {}),
              ...(event.detail !== undefined ? { detail: event.detail } : {}),
            },
      );
    case "input.required":
      return patchPart(state, event.partId, (part) =>
        part.type === "hitl_request" && part.status === "pending"
          ? { ...part, request: event.request, ...(event.expiresAt ? { expiresAt: event.expiresAt } : {}) }
          : part,
      );
    case "input.resolved":
      return patchPart(state, event.partId, (part) =>
        part.type === "hitl_request"
          ? {
              ...part,
              status: event.status,
              summary: event.summary,
              ...(event.answer ? { answer: event.answer } : {}),
            }
          : part,
      );
    case "tool.evidence":
      // Evidence is attached by the transcript layer, which owns its merge rules.
      return state;
    case "artifact.draft": {
      const existing = state.parts.find((part) => part.id === event.partId);
      if (existing) {
        // A later revision of the same draft, edited in the artifact panel.
        return patchPart(state, event.partId, (part) =>
          part.type === "artifact_preview" && event.draft.revision > part.draft.revision
            ? { ...part, draft: event.draft }
            : part,
        );
      }
      const part: ArtifactPart = { id: event.partId, seq: event.seq, state: "done", type: "artifact_preview", draft: event.draft };
      return { ...state, parts: insertBySeq(state.parts, part) };
    }
    case "save.suggested": {
      // One suggestion per turn.
      if (state.parts.some((part) => part.type === "save_suggestion")) return state;
      const part: SaveSuggestionPart = {
        id: event.partId,
        seq: event.seq,
        state: "done",
        type: "save_suggestion",
        draftRefs: event.draftRefs,
        capability: event.capability,
        reasonCode: event.reasonCode,
        message: event.message,
        saved: {},
      };
      return { ...state, parts: insertBySeq(state.parts, part) };
    }
    case "artifact.saved":
      return {
        ...state,
        parts: state.parts.map((part) =>
          part.type === "artifact_preview" && part.draft.draft_ref === event.draftRef
            ? { ...part, saved: event.saved }
            : part.type === "save_suggestion" && part.draftRefs.includes(event.draftRef)
              ? { ...part, saved: { ...part.saved, [event.draftRef]: event.saved } }
              : part,
        ),
      };
    case "role.changed": {
      // Work produced under the previous role was withdrawn by the server and is
      // not shown to the new role; the separator says why the turn restarted.
      const separator: RoleChangePart = {
        id: `${event.run_id ?? "run"}:role:${event.seq}`,
        seq: event.seq,
        state: "done",
        type: "role_change",
        activeRole: event.activeRole,
      };
      return { ...state, parts: [separator], suggestions: undefined, roleChangedTo: event.activeRole };
    }
    case "run.error": {
      // Tool failures already show on their parts. An answer that streamed
      // stays readable; a persistence failure is always surfaced.
      const answered = state.parts.some((part) => part.type === "text" && part.text);
      if (answered && event.code !== "run_persistence_unavailable") return state;
      return { ...state, error: event.message, status: "error" };
    }
    case "run.finished": {
      const parts = state.parts.map((part) => (part.state === "streaming" ? sealPart(part, event.finishReason) : part));
      const failed = !FINISHED.has(event.finishReason) && event.finishReason !== "cancelled";
      return {
        ...state,
        parts,
        status:
          state.status === "error" ? "error"
            : event.finishReason === "cancelled" ? "cancelled"
            : failed ? "error"
            : "done",
        stopReason: FINISHED.has(event.finishReason) ? undefined : event.finishReason,
        messageId: event.messageId || state.messageId,
        tokens: event.totalTokens ?? state.tokens,
        inputTokens: event.promptTokens ?? state.inputTokens,
        outputTokens: event.completionTokens ?? state.outputTokens,
      };
    }
    case "aux":
      return event.event.type === "suggestions" ? { ...state, suggestions: event.event.suggestions } : state;
    case "ping":
      return state;
    default:
      return state;
  }
}

/** Reduce a stored or captured event list from an empty turn. */
export function reduceEvents(events: StudioEvent[], initial: TurnRunState = emptyRunState()): TurnRunState {
  return events.reduce(turnReducer, initial);
}
