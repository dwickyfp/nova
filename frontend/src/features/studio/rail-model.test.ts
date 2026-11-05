import { expect, it } from "vitest";
import { branchStepText, formatSpan, groupRailSteps, recordedSpan, resultLine } from "./rail-model";
import type { RailStep } from "./thought-turn";

const step = (id: string, extra: Partial<RailStep>): RailStep => ({
  id, kind: "note", label: "plan", text: id, status: "done", ...extra,
});

const run: RailStep[] = [
  step("plan", { text: "Split the question" }),
  step("q", { kind: "delegate", label: "queued", text: "Called Finance", childRunId: "fin", actor: "Finance", body: "Quantify revenue" }),
  step("s", { kind: "delegate", label: "started", text: "Finance started working", childRunId: "fin", actor: "Finance" }),
  step("t", { kind: "tool", label: "query_execute", text: "Finance called query_execute", childRunId: "fin", actor: "Finance" }),
  step("th", { kind: "delegate", label: "thinking", text: "Finance: Checking margins", childRunId: "fin", actor: "Finance", status: "running" }),
];

it("gathers a specialist's rows under the row that called it", () => {
  const items = groupRailSteps(run, true);
  expect(items.map((item) => item.type)).toEqual(["step", "branch"]);
  const branch = items[1];
  if (branch.type !== "branch") throw new Error("expected a branch");
  expect(branch.head.id).toBe("q");
  // Lifecycle rows show through the branch mark, not as rows of their own.
  expect(branch.steps.map((item) => item.id)).toEqual(["t", "th"]);
  expect(branch.status).toBe("running");
  expect(branchStepText(branch.steps[1])).toBe("Checking margins");
});

it("settles a branch from its recorded end, never from the clock", () => {
  const done = groupRailSteps([...run.slice(0, 4), step("c", { kind: "delegate", label: "completed", text: "Finance finished", childRunId: "fin", detail: "Revenue grew 12%" })], false);
  expect(done[1]).toMatchObject({ type: "branch", status: "done", end: { detail: "Revenue grew 12%" } });

  const failed = groupRailSteps([...run.slice(0, 2), step("f", { kind: "delegate", label: "agent_failed", text: "Finance failed", childRunId: "fin", status: "failed" })], false);
  expect(failed[1]).toMatchObject({ status: "failed" });

  // A run that ended without the specialist finishing does not claim success.
  expect(groupRailSteps(run.slice(0, 3), false)[1]).toMatchObject({ status: "stopped" });
});

it("keeps a row from an unknown specialist as a plain step", () => {
  const items = groupRailSteps([step("m", { kind: "delegate", label: "message", text: "Ops messaged Main", childRunId: "ops" })], true);
  expect(items).toEqual([{ type: "step", id: "m", step: expect.objectContaining({ id: "m" }) }]);
});

it("formats spans and result lines for the rail", () => {
  expect(formatSpan(4730)).toBe("4.7s");
  expect(formatSpan(65_000)).toBe("1m 05s");
  expect(recordedSpan("2026-09-25T00:00:01", "2026-09-25T00:00:07")).toBe(6000);
  expect(recordedSpan("2026-09-25T00:00:07", "2026-09-25T00:00:01")).toBeUndefined();
  expect(recordedSpan(undefined, "2026-09-25T00:00:01")).toBeUndefined();
  expect(resultLine("## Revenue grew 12%\n\nDetails follow")).toBe("Revenue grew 12%");
  expect(resultLine("   ")).toBeUndefined();
  expect(resultLine("x".repeat(200))?.length).toBe(118);
});
