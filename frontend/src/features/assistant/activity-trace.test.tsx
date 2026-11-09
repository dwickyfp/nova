import { describe, expect, it } from "vitest";
import { render } from "vitest-browser-react";
import { ActivityTrace, railSteps } from "./activity-trace";
import type { TranscriptMessage } from "./use-assistant-transcript";

function activityMessage(
  overrides: Partial<TranscriptMessage> = {},
): TranscriptMessage {
  return {
    message_id: "a1",
    role: "activity",
    content: "",
    tool_call: null,
    created_at: "2026-09-19T00:00:00Z",
    activity_steps: [],
    activity_plan: [],
    ...overrides,
  };
}

describe("ActivityTrace", () => {
  it("renders nothing when there is no plan or activity", async () => {
    const { container } = await render(
      <ActivityTrace message={activityMessage()} />,
    );
    expect(container.textContent).toBe("");
  });

  it("folds a finished run into one summary line on Studio's rail", async () => {
    const screen = await render(
      <ActivityTrace
        message={activityMessage({
          activity_steps: [
            { key: "s1", phase: "skill", text: "Loading skill: create-table", status: "done" },
            { key: "s2", phase: "answer", text: "Writing the answer", status: "done" },
          ],
        })}
      />,
    );
    const toggle = screen.getByTestId("process-rail-toggle");
    await expect.element(toggle).toHaveAttribute("aria-expanded", "false");
    await expect.element(toggle).toHaveTextContent("Loaded a skill, Wrote the answer");
    await toggle.click();
    await expect.element(toggle).toHaveAttribute("aria-expanded", "true");
    await expect.element(screen.getByText("Loading skill: create-table")).toBeVisible();
  });

  it("opens while Neva is working and says what it is doing", async () => {
    const screen = await render(
      <ActivityTrace
        message={activityMessage({
          activity_steps: [
            { key: "s1", phase: "act", text: "Looking up the catalog", status: "running" },
          ],
        })}
      />,
    );
    await expect
      .element(screen.getByTestId("process-rail-toggle"))
      .toHaveAttribute("aria-expanded", "true");
  });

  it("maps steps, details and the plan onto rail rows", () => {
    expect(
      railSteps(activityMessage({
        activity_steps: [
          { key: "s1", phase: "observe", text: "Reading the result", status: "done", detail: "3 rows" },
        ],
      })),
    ).toEqual([
      { id: "s1", kind: "thinking", label: "observe", text: "Reading the result",
        status: "done", detail: "3 rows" },
    ]);
    expect(
      railSteps(activityMessage({
        activity_plan: [{ id: "understand", text: "Understand the request", status: "running" }],
      })),
    ).toEqual([
      { id: "understand", kind: "note", label: "plan", text: "Understand the request",
        status: "running" },
    ]);
  });
});
