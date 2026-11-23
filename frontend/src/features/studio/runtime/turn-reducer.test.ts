import { describe, expect, it } from "vitest";
import type { AgentMessage } from "@/features/agents/api";
import {
  applyStudioEvent,
  newTurn,
  replayThread,
  turnSegments,
  type TranscriptTurn,
} from "../studio-transcript";
import contract from "./protocol-v2-contract.json";
import { reduceEvents, turnReducer } from "./turn-reducer";
import { encodeV1Turn, parsedEvents, type V1Frame, type V2Record } from "./v2-test-encoder";
import type { StudioEvent } from "./protocol";

/**
 * The Studio turn reducer (protocol v2).
 *
 * What a reader depends on: a reopened conversation shows exactly what streamed
 * and in the same order, a resumed stream never duplicates a step, and a role
 * change leaves nothing from the previous role on screen.
 */

type Case = { name: string; run_id: string; role: string; v1: V1Frame[]; v2: V2Record[] };
const cases = contract.cases as unknown as Case[];
const [answerCase, roleCase, interruptedCase] = cases;

function live(events: StudioEvent[], id = "t"): TranscriptTurn {
  return events.reduce(
    (turns, event) => applyStudioEvent(turns, id, event),
    [newTurn({ id, question: "Q" })],
  )[0];
}

/**
 * The message the route stores: the frames delivered before the loop's final
 * `done` (the terminal and the seals it adds follow the write), without
 * keep-alives or Mission projections, and only from the last role change on.
 */
function storedMessage(item: Case, outcome = "stop"): AgentMessage {
  const done = item.v1.findIndex((frame) => frame.event === "done");
  const delivered = encodeV1Turn(item.v1.slice(0, done), { runId: item.run_id, role: item.role });
  let journal = delivered.filter((record) => record.event !== "ping" && record.event !== "mission_updated");
  const change = journal.map((record) => record.event).lastIndexOf("role.changed");
  if (change >= 0)
    journal = [...journal.slice(0, change).filter((record) => record.event === "run.started"), ...journal.slice(change)];
  return {
    message_id: String(item.v1[done].data.message_id),
    role: "assistant",
    content: "",
    created_at: "2026-11-23T00:00:00Z",
    steps: [
      { kind: "event_journal", protocol_version: 2, events: journal },
      { kind: "turn_outcome", finish_reason: outcome },
    ],
  } as unknown as AgentMessage;
}

function replayed(message: AgentMessage): TranscriptTurn {
  return replayThread([
    { message_id: "u", role: "user", content: "Q", created_at: "2026-11-23T00:00:00Z" } as AgentMessage,
    message,
  ])[0];
}

/** What a reader sees: ordered parts without per-client fields. */
function view(turn: TranscriptTurn) {
  return {
    parts: turn.run.parts.map(({ seq: _seq, ...part }) => part),
    steps: turn.steps,
    content: turn.content,
    state: turn.state,
    error: turn.error,
    stopReason: turn.stopReason,
  };
}

describe("protocol v2 contract", () => {
  it.each(cases.map((item) => [item.name, item] as const))(
    "the client test encoder matches the server encoder: %s",
    (_name, item) => {
      expect(encodeV1Turn(item.v1, { runId: item.run_id, role: item.role })).toEqual(item.v2);
    },
  );

  it("parses every server event", () => {
    for (const item of cases) expect(parsedEvents(item.v2)).toHaveLength(item.v2.length);
  });
});

describe("turn reducer", () => {
  it("orders parts by the seq of the event that opened them", () => {
    const turn = live(parsedEvents(answerCase.v2));
    const seqs = turn.run.parts.map((part) => part.seq);
    expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
    expect(turnSegments(turn.run.parts).map((segment) => segment.kind)).toEqual(["work", "hitl", "work", "content"]);
    expect(turn.content.map((item) => item.id)).toEqual(["content-0", "content-1", "content-2", "run-a#11"]);
    expect(turn.answer).toBe("Revenue rose 12%.");
    expect(turn.state).toBe("done");
    expect(turn.suggestions).toEqual(["Break it down by region"]);
  });

  it("ignores events at or below the last applied seq, so a resumed stream is idempotent", () => {
    const events = parsedEvents(answerCase.v2);
    const once = reduceEvents(events);
    // A reconnect replays from the start and again from the middle.
    const resumed = reduceEvents([...events.slice(0, 12), ...events, ...events.slice(8)]);
    expect(resumed).toEqual(once);
    expect(turnReducer(once, events[3])).toBe(once);
  });

  it("keeps one plan row when a step resumes after an answer, live and on replay", () => {
    const plan = [{ id: "u", text: "Understand the request", status: "running" }];
    const longer = [...plan, { id: "a", text: "Answer", status: "pending" }];
    const v1: V1Frame[] = [
      { event: "plan", data: { steps: plan } },
      { event: "plan", data: { steps: plan } },
      { event: "plan", data: { steps: longer } },
      { event: "done", data: { message_id: "m", finish_reason: "stop" } },
    ];
    const records = encodeV1Turn(v1);
    const planRows = (turn: TranscriptTurn) =>
      turn.run.parts.filter((part) => part.type === "reasoning" && part.phase === "plan");
    const streamed = live(parsedEvents(records));
    expect(planRows(streamed)).toEqual([
      expect.objectContaining({ planSteps: 2, text: "1. Understand the request\n2. Answer" }),
    ]);
    const reopened = replayed(storedMessage({ name: "plan", run_id: "run-1", role: "ANALYST", v1, v2: records }));
    expect(view(reopened)).toEqual(view(streamed));
    expect(planRows(reopened)).toHaveLength(1);
    // A second plan part (a run that restates its plan) takes the first one's place.
    const restated = parsedEvents(records);
    const start = restated.find((event) => event.type === "part.start")!;
    const again = { ...start, partId: "run-1#9", seq: 99 } as StudioEvent;
    const twice = reduceEvents([...restated.filter((event) => event.type !== "run.finished"), again]);
    expect(twice.parts.filter((part) => part.type === "reasoning" && part.phase === "plan")).toHaveLength(1);
  });

  it("produces the same turn live and from the stored journal", () => {
    for (const [item, outcome] of [[answerCase, "stop"], [roleCase, "stop"], [interruptedCase, "interrupted"]] as const) {
      const streamed = live(parsedEvents(item.v2));
      const reopened = replayed(storedMessage(item, outcome));
      expect(view(reopened), item.name).toEqual(view(streamed));
      expect(reopened.protocolVersion).toBe(2);
      expect(reopened.run.parts.map((part) => part.id)).toEqual(streamed.run.parts.map((part) => part.id));
    }
  });

  it("shows a separator and nothing from the previous role after a role change", () => {
    const earlier = newTurn({ id: "earlier", question: "Before" });
    const turns = parsedEvents(roleCase.v2).reduce(
      (current, event) => applyStudioEvent(current, "t", event),
      [earlier, newTurn({ id: "t", question: "Q" })],
    );
    expect(turns.map((turn) => turn.id)).toEqual(["t"]);
    const [turn] = turns;
    expect(turnSegments(turn.run.parts)[0]).toMatchObject({ kind: "role_change", activeRole: "MARKETING" });
    expect(turn.answer).toBe("Marketing answer.");
    expect(JSON.stringify(turn)).not.toContain("finance-secret");
    expect(turn.run.roleChangedTo).toBe("MARKETING");
  });

  it("closes a waiting call as not run and a running call as not confirmed when the run breaks off", () => {
    const turn = live(parsedEvents(interruptedCase.v2));
    const tools = turn.steps.filter((step) => step.kind === "tool");
    expect(tools.map((step) => [step.id, step.status, step.text])).toEqual([
      ["c1", "failed", "Result not confirmed"],
      ["c2", "cancelled", expect.any(String)],
    ]);
    // The approval it was waiting on can no longer be given.
    expect(turn.run.parts.find((part) => part.type === "hitl_request")).toMatchObject({
      status: "cancelled",
      state: "cancelled",
    });
    expect(turn.error).toBe("Progress could not be saved.");
    expect(turn.state).toBe("error");
    expect(turn.content.find((item) => item.type === "ui")?.complete).toBe(false);
  });

  it("keeps a Mission run's call apart from this run's call with the same id", () => {
    const turn = live(parsedEvents(answerCase.v2));
    const c1 = turn.run.parts.filter((part) => part.type === "tool" && part.toolCallId === "c1");
    expect(c1.map((part) => part.type === "tool" && part.originRunId)).toEqual([undefined, "mission-run"]);
  });
});
