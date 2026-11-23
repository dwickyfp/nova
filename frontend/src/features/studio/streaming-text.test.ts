import { describe, expect, it } from "vitest";
import { markdownBlocks } from "./streaming-text";
import { reasoningPreview } from "./rail-model";

describe("markdownBlocks", () => {
  it("splits at blank lines so finished blocks render once", () => {
    expect(markdownBlocks("First paragraph.\n\nSecond one\ncontinues.\n\n- a\n- b")).toEqual([
      "First paragraph.",
      "Second one\ncontinues.",
      "- a\n- b",
    ]);
  });

  it("keeps a fenced block and an indented continuation whole", () => {
    const text = "Intro\n\n```sql\nSELECT 1;\n\nSELECT 2;\n```\n\n1. Step\n\n   more of step one\n2. Next";
    expect(markdownBlocks(text)).toEqual([
      "Intro",
      "```sql\nSELECT 1;\n\nSELECT 2;\n```",
      "1. Step\n\n   more of step one\n2. Next",
    ]);
  });

  it("ignores leading and repeated blank lines", () => {
    expect(markdownBlocks("\n\nOne\n\n\n\nTwo\n")).toEqual(["One", "Two"]);
  });
});

describe("reasoningPreview", () => {
  it("shows the newest words while streaming and the first line once done", () => {
    const body = "**Calculating time difference**\n\nThe trip crosses one hour, so the span is shorter.";
    expect(reasoningPreview(body, false)).toBe("Calculating time difference");
    expect(reasoningPreview(body, true)).toBe("Calculating time difference The trip crosses one hour, so the span is shorter.");
    expect(reasoningPreview("x".repeat(300), true)).toMatch(/^…x{140}$/);
  });
});
