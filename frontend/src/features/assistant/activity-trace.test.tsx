import { describe, expect, it } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { ActivityTrace } from "./activity-trace";
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

  it("keeps a settled trace behind a plain activity disclosure", async () => {
    const { getByRole, container } = await render(
      <ActivityTrace
        message={activityMessage({
          activity_plan: [
            { id: "understand", text: "Understand", status: "done" },
          ],
          activity_steps: [
            {
              key: "s1",
              phase: "skill",
              text: "Loading skill: create-table",
              status: "done",
            },
          ],
        })}
      />,
    );
    const toggle = getByRole("button");
    await expect.element(toggle).toHaveAttribute("aria-expanded", "false");
    // The detailed lines are hidden until expanded.
    expect(container.textContent).toContain("Activity");
    expect(container.textContent).not.toContain("Loading skill: create-table");
    expect(
      container.querySelector('[data-role="activity"]')?.className,
    ).not.toContain("rounded-lg");
  });

  it("expands on click to show the plan and steps", async () => {
    const { getByRole, getByText } = await render(
      <ActivityTrace
        message={activityMessage({
          activity_plan: [
            {
              id: "understand",
              text: "Understand the request",
              status: "done",
            },
          ],
          activity_steps: [
            {
              key: "s1",
              phase: "skill",
              text: "Loading skill: create-table",
              status: "done",
            },
          ],
        })}
      />,
    );
    await getByRole("button").click();
    await expect
      .element(getByRole("button"))
      .toHaveAttribute("aria-expanded", "true");
    await expect
      .element(getByText("Understand the request"))
      .toBeInTheDocument();
  });

  it("shows only a small animated mark while the turn is running", async () => {
    const { getByRole, container } = await render(
      <ActivityTrace
        message={activityMessage({
          activity_steps: [
            {
              key: "s1",
              phase: "act",
              text: "Reasoning about the next step",
              status: "running",
            },
          ],
        })}
      />,
    );
    await expect
      .element(getByRole("button"))
      .toHaveAttribute("aria-expanded", "false");
    expect(container.querySelector(".animate-spin")).not.toBeNull();
    expect(container.textContent).not.toContain(
      "Reasoning about the next step",
    );
    getByRole("button").element().focus();
    await userEvent.keyboard("{Enter}");
    expect(container.textContent).toContain("Reasoning about the next step");
  });

  it("shows the mark as soon as a pending plan arrives", async () => {
    const { container } = await render(
      <ActivityTrace
        message={activityMessage({
          activity_plan: [{ id: "answer", text: "Answer", status: "pending" }],
        })}
      />,
    );
    expect(container.querySelector(".animate-spin")).not.toBeNull();
    expect(container.textContent).not.toContain("Answer");
  });
});
