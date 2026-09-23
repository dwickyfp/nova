import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { toast } from "sonner";
import { AnswerFooter, type AnswerFeedback } from "./answer-footer";
import "@/styles/index.css";

afterEach(() => vi.restoreAllMocks());

describe("AnswerFooter", () => {
  it("copies the answer and confirms success", async () => {
    const copy = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    await render(<AnswerFooter answer="Revenue is **42**." />);
    await page.getByRole("button", { name: "Copy answer" }).click();
    expect(copy).toHaveBeenCalledWith("Revenue is **42**.");
    await expect
      .element(page.getByRole("button", { name: "Copied", exact: true }))
      .toBeVisible();
  });

  it("can like, switch to dislike, and clear feedback", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    function Harness() {
      const [feedback, setFeedback] = useState<AnswerFeedback>(null);
      return (
        <AnswerFooter
          answer="Answer"
          feedback={feedback}
          onFeedback={async (next) => {
            await save(next);
            setFeedback(next);
          }}
        />
      );
    }
    await render(<Harness />);
    const like = page.getByRole("button", { name: "Like answer", exact: true });
    const dislike = page.getByRole("button", {
      name: "Dislike answer",
      exact: true,
    });
    await like.click();
    await expect.element(like).toHaveAttribute("aria-pressed", "true");
    await dislike.click();
    await expect.element(like).toHaveAttribute("aria-pressed", "false");
    await expect.element(dislike).toHaveAttribute("aria-pressed", "true");
    await dislike.click();
    await expect.element(dislike).toHaveAttribute("aria-pressed", "false");
    expect(save.mock.calls.map(([value]) => value)).toEqual([
      "like",
      "dislike",
      null,
    ]);
  });

  it("keeps the saved rating when a write fails", async () => {
    const error = vi.spyOn(toast, "error").mockImplementation(() => "error");
    await render(
      <AnswerFooter
        answer="Answer"
        feedback="like"
        onFeedback={async () => {
          throw new Error("offline");
        }}
      />,
    );
    await page.getByRole("button", { name: "Dislike answer" }).click();
    await expect
      .element(page.getByRole("button", { name: "Like answer", exact: true }))
      .toHaveAttribute("aria-pressed", "true");
    expect(error).toHaveBeenCalled();
  });

  it("shows reported token details including zero, without inventing missing usage", async () => {
    await render(
      <AnswerFooter
        answer="Answer"
        model="nova-model"
        tokens={1200}
        inputTokens={1200}
        outputTokens={0}
      />,
    );
    await expect.element(page.getByText("1,200 tokens")).toBeVisible();
    await page.getByRole("button", { name: "Answer details" }).click();
    await expect.element(page.getByText("Input tokens")).toBeVisible();
    await expect.element(page.getByText("Output tokens")).toBeVisible();
    await expect.element(page.getByText("0", { exact: true })).toBeVisible();
  });

  it("wraps the footer within a narrow viewport", async () => {
    await page.viewport(320, 640);
    try {
      await render(
        <div className="p-4">
          <AnswerFooter
            answer="Answer"
            model={"long-model-name-".repeat(15)}
            tokens={123456}
            onFeedback={async () => {}}
            onReconsider={() => {}}
          />
        </div>,
      );
      const footer = page
        .getByRole("group", { name: "Answer actions" })
        .element();
      expect(footer.scrollWidth).toBeLessThanOrEqual(footer.clientWidth);
      for (const button of footer.querySelectorAll("button")) {
        expect(button.getBoundingClientRect().right).toBeLessThanOrEqual(320);
      }
    } finally {
      await page.viewport(1280, 720);
    }
  });
});
