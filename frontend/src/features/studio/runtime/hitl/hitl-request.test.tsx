import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import type { HitlPart } from "../turn-reducer";
import { formErrors, initialValues } from "./form-values";
import { HitlRequest } from "./hitl-request";
import { parseHitlRequest } from "./parse";
import { hitlKind, registerHitlKind } from "./registry";
import type { ChoiceRequest, FormRequest, HitlRequestSpec } from "./types";

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

const automation = parseHitlRequest({
  kind: "approval",
  title: "Allow this action?",
  classification: "destructive",
  reason: "changes_data",
  role: "marketing",
  action: "Schedule the automation 'Weekly revenue' every Monday at 08:00",
})!;

describe("approval", () => {
  // Studio agents act without asking. Approvals stored by earlier versions
  // replay as past steps; nothing offers to answer them.
  it("never offers to answer an approval, even on a live run", async () => {
    const screen = await render(<HitlRequest part={part(automation)} live onAnswer={vi.fn()} />);
    expect(screen.getByRole("button").query()).toBeNull();
    expect(screen.container.textContent ?? "").not.toContain("Allow once");
  });

  it("replays a stored approval as a settled line with what it did", async () => {
    const allowed = await render(
      <HitlRequest
        part={part(automation, { status: "allowed", summary: "You allowed this action", state: "done" })}
        live={false}
        onAnswer={vi.fn()}
      />,
    );
    await allowed.getByRole("button", { name: /You allowed this action/ }).click();
    expect(allowed.container.textContent).toContain("Schedule the automation 'Weekly revenue' every Monday at 08:00");
    expect(allowed.getByRole("button", { name: "Allow once" }).query()).toBeNull();
  });

  it("replays an approval stored by an older version as a past step", async () => {
    const stored = parseHitlRequest({
      kind: "approval", title: "Approve this data step?", classification: "read_only",
      reason: "asks_first", role: "marketing", preview_kind: "semantic",
      preview: "Revenue by order status", allow_session: true,
      semantic: { question: "Revenue by order status", view: "Sales" },
    })!;
    expect(stored).toMatchObject({ kind: "approval", legacy: true, action: "Revenue by order status" });
    const pending = await render(<HitlRequest part={part(stored)} live onAnswer={vi.fn()} />);
    expect(pending.getByRole("button").query()).toBeNull();
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

  it("shows the suggested mark once, even when a badge repeats it", () => {
    const parsed = parseHitlRequest({
      kind: "choice", title: "Which plan?",
      options: [
        { id: "a", title: "A", badges: ["Recommended", "Trend"] },
        { id: "b", title: "B", recommended: true, badges: ["suggested"] },
      ],
    }) as ChoiceRequest;
    expect(parsed.options[0]).toMatchObject({ recommended: true, badges: ["Trend"] });
    expect(parsed.options[1].badges).toBeUndefined();
  });

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
});

describe("registry", () => {
  it("has the three built-in kinds, no approval, and accepts a new one", () => {
    for (const kind of ["question", "form", "choice"]) expect(hitlKind(kind)).toBeDefined();
    expect(hitlKind("approval")).toBeUndefined();
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
