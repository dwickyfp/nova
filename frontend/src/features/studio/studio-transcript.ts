import type { AssistantEvent, AutomationProposal, ChartBlock as ChartBlockType, CitationBlock, TableBlock, ToolCallView } from "@/features/assistant/types";
import type { AgentMessage } from "@/features/agents/api";
import type { RailStep } from "./thought-turn";
import type { AnswerFeedback } from "./answer-footer";
import type { SentAttachment } from "./studio-attachments";
import { evidenceHealthFromTrace, evidenceEnvelopeFromTrace, workflowProvenanceFromTrace, evidenceIdentity, mergeToolEvidence, readEvidenceEnvelope, readEvidenceHealth, type ToolEvidence } from "./evidence-health";

export type StudioAssistantEvent = AssistantEvent;

/** A tool call with the id this conversation uses to resolve it. */
export type PendingCall = ToolCallView & { tool_call_id: string };

export type OrderedContent =
  | {
      id: string;
      index: number;
      type: "text";
      text: string;
      complete: boolean;
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
  /** A pending tool call waiting on the user's decision, if any. */
  pendingConsent: PendingCall | null;
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
  /** A schedule Smart drafted with this answer, waiting for the user to confirm. */
  automationProposal?: AutomationProposal;
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
};


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
        pendingConsent: null,
        blocks: { tables: [], charts: [], citations: [] },
        state: "done",
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
        pendingConsent: null,
        blocks: { tables: [], charts: [], citations: [] },
        state: "done",
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
          const workflowProvenance = workflowProvenanceFromTrace(step) ?? undefined;
          if (health) open.evidence = mergeToolEvidence(open.evidence ?? [], {
            toolCallId: step.tool_call_id || `${step.name}-${open.steps.length}`,
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
            preview: step.name === "load_skill" ? undefined : step.preview || undefined,
            detail: step.name === "load_skill" ? undefined : step.detail || undefined,
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
              title: step.title,
              columns: step.columns,
              rows: step.rows,
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
        case "automation_proposal":
          {
            const { kind: _kind, ...proposal } = step;
            open.automationProposal = proposal;
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
 * Return the contiguous authored prefix that is safe to display.
 *
 * The current block is visible while it streams. A higher index is visible
 * only after every preceding block exists and is sealed. This is the client
 * side of the ordering barrier: a table at index 1 waits for text at index 0,
 * while a table intentionally authored at index 0 appears immediately.
 */
let blockSeq = 0;
function nextBlockId(): string {
  blockSeq += 1;
  return `block-${blockSeq}`;
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

function nextLegacyArtifactIndex(content: OrderedContent[]): number {
  const highestArtifact = content.reduce(
    (highest, item) =>
      item.type === "text" ? highest : Math.max(highest, item.index),
    0,
  );
  return highestArtifact + 1;
}

/**
 * Fold one SSE event into the turn it belongs to.
 *
 * The rules that matter:
 *
 *  - `text_delta` appends to the turn's answer, so it streams in place.
 *  - `thinking` becomes a rail row. A frame that repeats the phase already live
 *    replaces it rather than stacking, because the loop re-announces `act` on
 *    every iteration and a row per iteration would bury the real steps.
 *  - `tool_call` both opens a rail row and, when consent is required, raises the
 *    card. The card is what the turn is waiting on; the row records it either way.
 *  - a status frame updates its row in place, matched by tool call id.
 *  - result blocks are keyed so React keeps them stable across re-renders.
 */
export function applyEvent(
  turns: TranscriptTurn[],
  turnId: string,
  event: StudioAssistantEvent,
): TranscriptTurn[] {
  if (event.type === "role_changed") {
    return turns
      .filter((turn) => turn.id === turnId)
      .map((turn) => ({
        ...turn,
        answer: "",
        content: [],
        steps: [],
        evidence: [],
        toolPreviews: {},
        pendingConsent: null,
        blocks: { tables: [], charts: [], citations: [] },
      }));
  }
  return patchTurn(turns, turnId, (turn) => {
    switch (event.type) {
      case "evidence_envelope":
      case "evidence_health": {
        const envelope = event.type === "evidence_envelope" ? readEvidenceEnvelope(event.payload) : null;
        const health = envelope?.health ?? (event.type === "evidence_health" ? readEvidenceHealth(event.payload) : null);
        if (!health) return turn;
        const workflowProvenance = workflowProvenanceFromTrace(event) ?? undefined;
        if ((event.workflow ?? event.workflow_provenance) != null && !workflowProvenance) return turn;
        const step = turn.steps.find((item) => item.id === event.tool_call_id);
        const runId = workflowProvenance?.run_id ?? event.run_id;
        const preview = turn.toolPreviews?.[evidenceIdentity({ toolCallId: event.tool_call_id, runId })] ?? (!runId ? step?.preview : undefined);
        return { ...turn, evidence: mergeToolEvidence(turn.evidence ?? [], {
          toolCallId: event.tool_call_id,
          toolName: event.tool_name ?? step?.label ?? "Query",
          health,
          ...(envelope ? { envelope } : {}),
          ...(workflowProvenance ? { workflowProvenance } : {}),
          runId,
          ...(preview ? { sqlPreview: preview } : {}),
        }) };
      }
      case "plan": {
        const plan: RailStep = {
          id: "execution-plan",
          kind: "note",
          label: "plan",
          text: `Planned ${event.steps.length} step${event.steps.length === 1 ? "" : "s"}`,
          status: "done",
          body: event.steps
            .map((step, index) => `${index + 1}. ${step.text}`)
            .join("\n"),
        };
        const existing = turn.steps.findIndex((step) => step.id === plan.id);
        return {
          ...turn,
          steps:
            existing === -1
              ? [...turn.steps, plan]
              : turn.steps.map((step, index) =>
                  index === existing ? plan : step,
                ),
        };
      }

      case "thinking": {
        let existing = -1;
        for (let index = turn.steps.length - 1; index >= 0; index -= 1) {
          const step = turn.steps[index];
          if (
            step.kind === "thinking" &&
            step.label === event.phase &&
            step.status === "running"
          ) {
            existing = index;
            break;
          }
        }
        const id =
          existing === -1
            ? uniqueStepId(turn.steps, `think-${event.phase}`)
            : turn.steps[existing].id;
        const row: RailStep = {
          id,
          kind: "thinking",
          label: event.phase,
          text: event.text,
          status: event.status,
          // Every phase carries its own text as the row's body, so any step can
          // be opened to read what it did. The `act` row is the only one the
          // loop re-announces with real narration, but a plan, a skill load and
          // an observation each say something worth keeping.
          body: event.text,
        };
        const steps =
          existing === -1
            ? [...turn.steps, row]
            : turn.steps.map((step, i) =>
                i === existing ? { ...row, body: step.body ?? row.body } : step,
              );
        // A later phase means the earlier one is finished. The loop announces
        // ``act`` on every iteration without closing it, so an unclosed row would
        // otherwise keep its spinner forever once the turn settles.
        const PHASE_ORDER = ["plan", "skill", "act", "observe", "answer"];
        const rank = (step: RailStep) =>
          step.kind === "thinking" ? PHASE_ORDER.indexOf(step.label) : -1;
        const current = rank(row);
        const settled = steps.map((step) =>
          step.kind === "thinking" &&
          step.status === "running" &&
          current > -1 &&
          rank(step) > -1 &&
          rank(step) < current
            ? { ...step, status: "done" as const }
            : step,
        );
        return { ...turn, steps: settled };
      }

      case "tool_call": {
        const { payload } = event;
        const row: RailStep = {
          id: payload.tool_call_id,
          kind: "tool",
          label: payload.tool_name,
          skillName: payload.skill_name || undefined,
          text:
            payload.tool_name === "load_skill"
              ? describeStatus(payload.tool_name, payload.status, payload.skill_name)
              : describeTool(payload.tool_name),
          status: payload.status,
          preview:
            payload.tool_name === "load_skill"
              ? undefined
              : payload.sql_preview || undefined,
        };
        const existing = turn.steps.findIndex(
          (s) => s.id === payload.tool_call_id,
        );
        return {
          ...turn,
          ...(row.preview ? { toolPreviews: { ...turn.toolPreviews, [evidenceIdentity({ toolCallId: payload.tool_call_id, runId: event.run_id })]: row.preview } } : {}),
          steps:
            existing === -1
              ? [...turn.steps, row]
              : turn.steps.map((s, i) => (i === existing ? row : s)),
          // A `pending` call is the one the turn is blocked on.
          pendingConsent:
            payload.status === "pending" ? payload : turn.pendingConsent,
        };
      }

      case "tool_status": {
        const existing = turn.steps.findIndex(
          (step) => step.id === event.tool_call_id,
        );
        // A call that needed no approval never got a `tool_call` frame carrying
        // its preview in older streams; a status frame alone must still produce
        // a row, or the tool and its SQL would vanish from the rail. The raw
        // call id is never shown: it is a provider artifact, not a tool name.
        const steps =
          existing === -1
            ? [
                ...turn.steps,
                {
                  id: event.tool_call_id,
                  kind: "tool" as const,
                  label: "tool",
                  text: describeStatus("tool", event.status),
                  status: event.status,
                },
              ]
            : turn.steps.map((step) =>
                step.id === event.tool_call_id
                  ? {
                      ...step,
                      status: event.status,
                       text: describeStatus(step.label, event.status, step.skillName),
                    }
                  : step,
              );
        const pendingConsent =
          turn.pendingConsent &&
          turn.pendingConsent.tool_call_id === event.tool_call_id &&
          event.status !== "pending"
            ? null
            : turn.pendingConsent;
        return { ...turn, steps, pendingConsent };
      }

      case "tool_progress":
        return {
          ...turn,
          ...(event.sql_preview ? {
            toolPreviews: { ...turn.toolPreviews, [evidenceIdentity({ toolCallId: event.tool_call_id, runId: event.run_id })]: event.sql_preview },
            evidence: turn.evidence?.map((item) => evidenceIdentity(item) === evidenceIdentity({ toolCallId: event.tool_call_id, runId: event.run_id }) ? { ...item, sqlPreview: event.sql_preview } : item),
          } : {}),
          steps: turn.steps.map((step) =>
            step.id === event.tool_call_id
              ? {
                  ...step,
                  text: event.text,
                  preview:
                    step.label === "load_skill"
                      ? undefined
                      : event.sql_preview ?? step.preview,
                  status:
                    event.stage === "query_completed"
                      ? ("done" as const)
                      : ("running" as const),
                }
              : step,
          ),
        };

      case "text_delta": {
        const currentContent = turn.content ?? [];
        const index = event.content_index ?? 0;
        const id = event.content_id ?? "legacy-text-0";
        const existing = currentContent.findIndex(
          (item) =>
            item.id === id || (item.type === "text" && item.index === index),
        );
        const content =
          existing === -1
            ? [
                ...currentContent,
                {
                  id,
                  index,
                  type: "text" as const,
                  text: event.text,
                  complete: false,
                },
              ]
            : currentContent.map((item, itemIndex) =>
                itemIndex === existing && item.type === "text"
                  ? { ...item, text: item.text + event.text }
                  : item,
              );
        return { ...turn, answer: turn.answer + event.text, content };
      }

      case "table": {
        const currentContent = turn.content ?? [];
        const id = event.content_id ?? nextBlockId();
        const index =
          event.content_index ?? nextLegacyArtifactIndex(currentContent);
        return {
          ...turn,
          content: [
            ...currentContent,
            {
              id,
              index,
              type: "table" as const,
              block: event.payload,
              complete: true as const,
            },
          ],
          blocks: {
            ...turn.blocks,
            tables: [...turn.blocks.tables, { id, block: event.payload }],
          },
        };
      }

      case "chart": {
        const currentContent = turn.content ?? [];
        const id = event.content_id ?? nextBlockId();
        const index =
          event.content_index ?? nextLegacyArtifactIndex(currentContent);
        return {
          ...turn,
          content: [
            ...currentContent,
            {
              id,
              index,
              type: "chart" as const,
              block: event.payload,
              complete: true as const,
            },
          ],
          blocks: {
            ...turn.blocks,
            charts: [...turn.blocks.charts, { id, block: event.payload }],
          },
        };
      }

      case "citation": {
        const currentContent = turn.content ?? [];
        const id = event.content_id ?? nextBlockId();
        const index =
          event.content_index ?? nextLegacyArtifactIndex(currentContent);
        return {
          ...turn,
          content: [
            ...currentContent,
            {
              id,
              index,
              type: "citation" as const,
              block: event.payload,
              complete: true as const,
            },
          ],
          blocks: {
            ...turn.blocks,
            citations: [...turn.blocks.citations, event.payload],
          },
        };
      }

      case "content_block_done":
        return {
          ...turn,
          content: (turn.content ?? []).map((item) =>
            item.id === event.content_id || item.index === event.content_index
              ? { ...item, complete: true }
              : item,
          ),
        };

      case "tool_detail": {
        const health = evidenceHealthFromTrace(event);
        return {
          ...turn,
          ...(health ? { evidence: mergeToolEvidence(turn.evidence ?? [], {
            toolCallId: event.tool_call_id, toolName: turn.steps.find((step) => step.id === event.tool_call_id)?.label ?? "Query", health,
            runId: event.run_id,
          }) } : {}),
          steps: turn.steps.map((step) =>
            step.id === event.tool_call_id && step.label !== "load_skill"
              ? { ...step, detail: event.text }
              : step,
          ),
        };
      }

      case "error":
        // A denied or failed tool is reported through both an error frame and a
        // status; the rail row already carries it, so the turn only records the
        // message when there is no answer to show alongside it.
        return {
          ...turn,
          state: turn.answer ? turn.state : "error",
          error: turn.answer ? turn.error : event.message,
        };

      case "suggestions":
        return { ...turn, suggestions: event.suggestions };

      case "automation_proposal":
        return { ...turn, automationProposal: event.proposal };

      case "done":
        return {
          ...turn,
          // The turn is over: nothing on the rail can still be running, whatever
          // a missed close frame left behind. This is the backstop that stops a
          // spinner outliving the answer.
          steps: turn.steps.map((step) =>
            step.status === "running"
              ? { ...step, status: "done" as const }
              : step,
          ),
          content: (turn.content ?? []).map((item) => ({
            ...item,
            complete: true,
          })),
          state:
            turn.state === "error"
              ? "error"
              : event.finish_reason === "cancelled"
                ? "cancelled"
                : turn.state,
          stopReason:
            event.finish_reason === "stop" ? undefined : event.finish_reason,
          tokens: event.total_tokens ?? turn.tokens,
          messageId: event.message_id,
          inputTokens: event.prompt_tokens ?? turn.inputTokens,
          outputTokens: event.completion_tokens ?? turn.outputTokens,
        };

      default:
        return turn;
    }
  });
}

/** A tool name turned into the line a reader understands. */
function describeTool(name: string, skillName?: string | null): string {
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
    default:
      return `Called ${name}`;
  }
}

function describeStatus(
  name: string,
  status: string,
  skillName?: string | null,
): string {
  switch (status) {
    case "running":
      return name === "load_skill" && skillName
        ? `Loading skill: ${skillName}`
        : `Running ${name}`;
    case "done":
      return describeTool(name, skillName);
    case "failed":
      return `${name} failed`;
    case "denied":
      return `${name} was denied`;
    case "cancelled":
      return `${name} was cancelled`;
    default:
      return describeTool(name);
  }
}
