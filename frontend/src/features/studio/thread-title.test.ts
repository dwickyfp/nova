import { expect, it } from "vitest";
import { threadTitle } from "./thread-title";

it("keeps a short first message as the title", () => {
  expect(threadTitle("  What was\n total revenue?  ")).toBe("What was total revenue?");
});

it("shortens a long first message so the thread can be created", () => {
  const title = threadTitle(`Look into this change from News: ${"revenue ".repeat(80)}`);

  expect(title.length).toBeLessThanOrEqual(120);
  expect(title.endsWith("…")).toBe(true);
  expect(title.startsWith("Look into this change from News")).toBe(true);
});
