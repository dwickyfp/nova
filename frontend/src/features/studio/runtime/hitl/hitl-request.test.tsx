import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "@vitest/browser/context";
import type { HitlPart } from "../turn-reducer";
import { formErrors, initialValues } from "./form-values";
import { HitlRequest } from "./hitl-request";
import { parseHitlRequest } from "./parse";
import { hitlKind, registerHitlKind } from "./registry";
import type { FormRequest, HitlRequestSpec } from "./types";

function part(request: HitlRequestSpec, extra: Partial<HitlPart> = {}): HitlPart {
  return {
    id: "run:3",
    seq: 3,
    state: "streaming",
    type: "hitl_request",
    requestId: "req-1",
    kind: request.kind,
    request,
    status: "pending",
    ...extra,
  };
}

const semantic = parseHitlRequest({
  kind: "approval",
  title: "Approve this data step?",
  classification: "read_only",
  reason: "asks_first",
  role: "marketing",
  preview_kind: "semantic",
  preview: "Revenue by order status",
  allow_session: true,
  objects: ["Sales"],
  semantic: { question: "Revenue by order status", view: "Sales" },
  editable: { argument: "question", label: "Question", max_length: 2000 },
})!;

describe("approval", () => {
  it("labels a semantic request as one, says why it asks and never calls it SQL", async () => {
    const screen = await render(<HitlRequest part={part(semantic)} live onAnswer={vi.fn()} />);
    const text = screen.container.textContent ?? "";
    expect(text).toContain("Semantic request");
    expect(text).toContain("Semantic View");
    expect(text).toContain("Runs as marketing");
    expect(text).toContain("Read-only");
    expect(text).toContain("This agent asks before each data step");
    expect(text).toContain("The exact SQL appears on the step after it runs");
    expect(text).not.toMatch(/\bSQL that will run\b/);
  });

  it("sends Allow once, Allow for this chat and Deny", async () => {
    for (const [label, action] of [
      ["Allow once", "allow_once"],
      ["Allow for this chat", "allow_session"],
      ["Deny", "deny"],
    ] as const) {
      const onAnswer = vi.fn().mockResolvedValue(undefined);
      const screen = await render(<HitlRequest part={part(semantic)} live onAnswer={onAnswer} />);
      await screen.getByRole("button", { name: label }).click();
      expect(onAnswer).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ requestId: "req-1" }), { action });
      await screen.unmount();
    }
  });

  it("Edit then run sends only the edited argument, and only when it changed", async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    const screen = await render(<HitlRequest part={part(semantic)} live onAnswer={onAnswer} />);
    await screen.getByRole("button", { name: "Edit then run" }).click();
    const run = screen.getByRole("button", { name: "Run edited request" });
    expect(run.element()).toBeDisabled();
    const editor = screen.getByLabelText("Edit the question");
    await editor.fill("Net revenue by order status");
    await run.click();
    expect(onAnswer).toHaveBeenCalledExactlyOnceWith(expect.anything(), {
      action: "allow_once",
      edited_arguments: { question: "Net revenue by order status" },
    });
  });

  it("offers no chat-wide grant for a change to data and shows its SQL as code", async () => {
    const request = parseHitlRequest({
      kind: "approval", title: "Approve this change to data?", classification: "destructive",
      reason: "changes_data", role: "analyst", preview_kind: "sql",
      preview: "DELETE FROM sales.orders WHERE id = 1", allow_session: false,
    })!;
    const screen = await render(<HitlRequest part={part(request)} live onAnswer={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Allow for this chat" }).query()).toBeNull();
    expect(screen.getByText("DELETE FROM sales.orders WHERE id = 1").element().closest("pre")).not.toBeNull();
    expect(screen.container.textContent).toContain("Changes data");
  });

  it("shows the server's refusal and keeps the request answerable", async () => {
    const onAnswer = vi.fn().mockRejectedValueOnce(new Error("This request is no longer waiting."));
    const screen = await render(<HitlRequest part={part(semantic)} live onAnswer={onAnswer} />);
    await screen.getByRole("button", { name: "Allow once" }).click();
    await expect.element(screen.getByRole("alert")).toHaveTextContent("This request is no longer waiting.");
    expect(screen.getByRole("button", { name: "Allow once" }).element()).toBeEnabled();
  });
});

describe("question", () => {
  const question = parseHitlRequest({
    kind: "question", title: "Gross or net revenue?", detail: "Net excludes refunds.",
    choices: ["Gross revenue", "Net revenue"], allow_free_text: true,
  })!;

  it("answers with a chip or with typed text (Enter sends)", async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    const chips = await render(<HitlRequest part={part(question)} live onAnswer={onAnswer} />);
    await chips.getByRole("button", { name: "Net revenue" }).click();
    expect(onAnswer).toHaveBeenLastCalledWith(expect.anything(), { action: "answer", answer: { text: "Net revenue" } });
    // Sent: the card waits for the run to confirm, and takes no second answer.
    expect(chips.getByRole("button", { name: "Gross revenue" }).element()).toBeDisabled();
    expect(chips.getByRole("status").element()).toHaveTextContent("Sending your answer");
    await chips.unmount();
    const screen = await render(<HitlRequest part={part(question)} live onAnswer={onAnswer} />);
    await screen.getByLabelText("Your answer").fill("Net of tax");
    await userEvent.keyboard("{Enter}");
    expect(onAnswer).toHaveBeenLastCalledWith(expect.anything(), { action: "answer", answer: { text: "Net of tax" } });
  });

  it("can be skipped", async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    const screen = await render(<HitlRequest part={part(question)} live onAnswer={onAnswer} />);
    await screen.getByRole("button", { name: /Skip/ }).click();
    expect(onAnswer).toHaveBeenCalledExactlyOnceWith(expect.anything(), { action: "decline" });
  });
});

describe("choice", () => {
  const choice = parseHitlRequest({
    kind: "choice", title: "Which analysis plan?",
    options: [
      { id: "region", title: "By region", description: "Revenue per region" },
      { id: "month", title: "By month", recommended: true, badges: ["Trend"] },
      { id: "channel", title: "By channel" },
    ],
  })!;

  it("is a radio group that starts on the suggested option and moves with arrow keys", async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    const screen = await render(<HitlRequest part={part(choice)} live onAnswer={onAnswer} />);
    const month = screen.getByRole("radio", { name: /By month/ });
    expect(month.element()).toHaveAttribute("aria-checked", "true");
    (month.element() as HTMLElement).focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("radio", { name: /By channel/ }).element()).toHaveAttribute("aria-checked", "true");
    await screen.getByRole("button", { name: "Continue with this option" }).click();
    expect(onAnswer).toHaveBeenCalledExactlyOnceWith(expect.anything(), {
      action: "answer",
      answer: { option_id: "channel" },
    });
  });
});

describe("form", () => {
  const form = parseHitlRequest({
    kind: "form", title: "Which period and region?",
    fields: [
      { name: "period", title: "Period", widget: "date_range" },
      { name: "top", title: "Top N", widget: "integer", minimum: 1, maximum: 50 },
      { name: "refunds", title: "Include refunds", widget: "toggle" },
    ],
    required: ["period"],
  })! as FormRequest;

  it("validates before sending and sends typed values", async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    const screen = await render(<HitlRequest part={part(form)} live onAnswer={onAnswer} />);
    await screen.getByRole("button", { name: "Continue" }).click();
    expect(onAnswer).not.toHaveBeenCalled();
    expect(screen.container.textContent).toContain("Period is required.");
    await screen.getByLabelText("Period start").fill("2026-10-01");
    await screen.getByLabelText("Period end").fill("2026-10-31");
    await screen.getByLabelText("Top N").fill("10");
    await screen.getByRole("button", { name: "Continue" }).click();
    expect(onAnswer).toHaveBeenCalledExactlyOnceWith(expect.anything(), {
      action: "answer",
      answer: { values: { period: { start: "2026-10-01", end: "2026-10-31" }, top: 10, refunds: false } },
    });
  });

  it("mirrors the server's field rules", () => {
    const values = initialValues(form);
    expect(formErrors(form, { ...values, period: { start: "2026-10-31", end: "2026-10-01" } }).period)
      .toBe("The start date is after the end date.");
    expect(formErrors(form, { ...values, period: { start: "2026-02-30", end: "2026-03-01" } }).period).toBeDefined();
    expect(formErrors(form, { ...values, period: { start: "2026-10-01", end: "2026-10-02" }, top: "51" }).top)
      .toBe("Top N must be at most 50.");
    expect(formErrors(form, { ...values, period: { start: "2026-10-01", end: "2026-10-02" }, top: "2.5" }).top)
      .toBe("Top N must be a whole number.");
  });
});

describe("settled and unreachable requests", () => {
  it("collapses an answer to one line that expands to what was asked", async () => {
    const question = parseHitlRequest({ kind: "question", title: "Gross or net?", choices: ["Gross", "Net"] })!;
    const screen = await render(
      <HitlRequest
        part={part(question, { status: "answered", summary: "You answered: Net", state: "done" })}
        live
        onAnswer={vi.fn()}
      />,
    );
    const line = screen.getByRole("button", { name: /You answered: Net/ });
    expect(line.element()).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Net", exact: true }).query()).toBeNull();
    await line.click();
    expect(screen.container.textContent).toContain("Gross or net?");
  });

  it("never offers controls once the run is gone", async () => {
    const question = parseHitlRequest({ kind: "question", title: "Gross or net?", choices: ["Gross", "Net"] })!;
    const replay = await render(<HitlRequest part={part(question)} live={false} onAnswer={vi.fn()} />);
    expect(replay.getByRole("button").query()).toBeNull();
    await replay.unmount();
    const stopped = await render(
      <HitlRequest part={part(question, { status: "cancelled", state: "cancelled" })} live onAnswer={vi.fn()} />,
    );
    expect(stopped.container.textContent).toContain("Stopped before you answered");
  });

  it("shows why an edited request was refused", async () => {
    const screen = await render(
      <HitlRequest
        part={part(semantic, {
          status: "refused", summary: "Nova refused the edited request",
          answer: { reason: "Nova policy does not allow the edited request." }, state: "done",
        })}
        live
        onAnswer={vi.fn()}
      />,
    );
    await screen.getByRole("button", { name: /Nova refused the edited request/ }).click();
    expect(screen.container.textContent).toContain("Nova policy does not allow the edited request.");
  });
});

describe("registry", () => {
  it("has the four built-in kinds and accepts a new one", () => {
    for (const kind of ["approval", "question", "form", "choice"]) expect(hitlKind(kind)).toBeDefined();
    registerHitlKind({
      kind: "question",
      label: "Question",
      Icon: hitlKind("question")!.Icon,
      Body: hitlKind("question")!.Body,
      skippable: true,
    });
    expect(hitlKind("question")?.label).toBe("Question");
  });

  it("drops malformed requests instead of guessing", () => {
    expect(parseHitlRequest({ kind: "choice", title: "x", options: [{ id: "a", title: "A" }] })).toBeNull();
    expect(parseHitlRequest({ kind: "question", title: "x", choices: [], allow_free_text: false })).toBeNull();
    expect(parseHitlRequest({ kind: "form", title: "x", fields: [{ name: "f", title: "F", widget: "file" }] })).toBeNull();
    expect(parseHitlRequest({ kind: "approval", classification: "denied" })).toBeNull();
  });
});
