import { describe, expect, it } from "vitest";
import { processSummary } from "./process-summary";
import { parseStudioRecord } from "./runtime/protocol";
import { reduceEvents } from "./runtime/turn-reducer";
import { describeTool, stepsFromParts } from "./studio-transcript";

/**
 * An MCP connector tool reaches the model under an opaque identifier
 * (`mcp_<id>`). The rail must name it the way people know it, never by that id.
 */
describe("MCP tool steps in the rail", () => {
  const start = (extra: Record<string, unknown> = {}) =>
    parseStudioRecord("part.start", {
      run_id: "r",
      seq: 1,
      part_id: "r#1",
      type: "tool",
      tool_call_id: "c1",
      tool_name: "mcp_3f2a9b0c4d5e6f708192a3b4c5d6e7f8",
      display_name: "Nova test tools: fx convert",
      ...extra,
    });

  it("reads the display name from the part", () => {
    const event = start();
    expect(event).not.toBeNull();
    const parts = reduceEvents([event!]).parts;
    const [step] = stepsFromParts(parts);
    expect(step.text).toBe("Using Nova test tools: fx convert");
    expect(step.text).not.toContain("mcp_");
  });

  it("names the finished step and the summary plainly", () => {
    const done = parseStudioRecord("tool.status", { run_id: "r", seq: 2, part_id: "r#1", status: "done" });
    const parts = reduceEvents([start()!, done!]).parts;
    const steps = stepsFromParts(parts);
    expect(steps[0].text).toBe("Used Nova test tools: fx convert");
    expect(processSummary(steps, false)).toBe("Used Nova test tools: fx convert");
  });

  it("keeps the established labels for Nova's own tools", () => {
    expect(describeTool("query_execute", null, "ignored")).toBe("Ran a SQL query");
  });
});
