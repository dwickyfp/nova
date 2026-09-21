import { api, apiBase, authHeaders } from "@/lib/api-client";
import { parseAssistantEvent, readSseFrames } from "./events";
import type {
  AssistantEvent,
  ConsentDecision,
  ConsentDecisionPayload,
} from "./types";

export type StreamTurnOptions = {
  signal?: AbortSignal;
  onEvent: (event: AssistantEvent) => void;
};

/** Active worksheet context the assistant reasons against for one turn. */
export type TurnContext = {
  database?: string | null;
  schema?: string | null;
  role?: string | null;
  /**
   * Model (and its provider) pinned for this turn by the panel's selector.
   * Omitted lets the backend use the provider's first active model.
   */
  model?: string | null;
  providerId?: string | null;
};

/**
 * Opens one assistant turn and drives the caller's reducer with typed events.
 * Contract: docs/specs/nova-61-agentic-assistant-design.md §4.
 *
 * `fetch` + `ReadableStream` rather than `EventSource`, because Nova's bearer
 * token lives in Zustand and `EventSource` cannot send an Authorization header.
 * No auto-reconnect: a replayed stream could duplicate a tool call.
 *
 * The backend stores the user message itself when the stream starts, so the
 * client renders it optimistically and must not send it a second time.
 */
export async function streamAssistantTurn(
  threadId: string,
  content: string,
  {
    signal,
    onEvent,
    database,
    schema,
    role,
    model,
    providerId,
  }: StreamTurnOptions & TurnContext,
): Promise<void> {
  const response = await fetch(
    `${apiBase()}/assistant/threads/${encodeURIComponent(threadId)}/messages`,
    {
      method: "POST",
      headers: authHeaders({
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      }),
      body: JSON.stringify({
        content,
        database,
        schema,
        role,
        model,
        provider_id: providerId,
      }),
      signal,
    },
  );

  if (response.status === 401) {
    throw new Error("Session expired");
  }
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined);
    throw new Error(detail || "The assistant request failed");
  }
  if (!response.body) {
    throw new Error("The assistant returned an empty stream");
  }

  let activeRun: string | undefined;
  let lastSequence = -1;
  for await (const frame of readSseFrames(response.body)) {
    const event = parseAssistantEvent(frame.event, frame.data);
    if (!event) continue;
    if (event.run_id && event.run_id !== activeRun) {
      activeRun = event.run_id;
      lastSequence = -1;
    }
    if (event.sequence !== undefined) {
      if (event.sequence <= lastSequence) continue;
      lastSequence = event.sequence;
    }
    onEvent(event);
  }
}

/** Maps the card's two-part intent to the frozen wire enum (spec §6.1). */
export function toConsentPayload(
  decision: ConsentDecision,
  alwaysAllow: boolean,
): ConsentDecisionPayload {
  if (decision === "deny") return "deny";
  return alwaysAllow ? "allow_session" : "allow_once";
}

export type ConsentDecisionResponse = {
  tool_call_id: string;
  status: string;
  /** True only when this decision set the conversation's read-only grant. */
  grant_active: boolean;
};

/**
 * Consent is a separate HTTP call, not a frame on the stream (§4, §6). The
 * still-open stream then emits `tool_status` once the decision is applied.
 *
 * The path is `tool-calls/{id}/decision`, scoped by call id only: no thread
 * segment, and the body carries the enum, not an `always_allow` flag (§6.1).
 * The response's `grant_active` is the only signal that a grant is now live,
 * so it is returned rather than discarded; the panel needs it to offer reset.
 */
export async function decideToolCall(
  toolCallId: string,
  decision: ConsentDecision,
  alwaysAllow = false,
): Promise<ConsentDecisionResponse> {
  return api.post<ConsentDecisionResponse>(
    `/assistant/tool-calls/${encodeURIComponent(toolCallId)}/decision`,
    { decision: toConsentPayload(decision, alwaysAllow) },
  );
}
