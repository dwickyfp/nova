import { describe, expect, it } from "vitest";
import type { TranscriptTurn } from "./studio-chat";
import {
  findArtifactSql,
  formatArtifactSql,
  uiNodeContentId,
  type SavableContent,
} from "./artifact-source";

function turn(id: string, steps: TranscriptTurn["steps"]): TranscriptTurn {
  return {
    id,
    question: "Revenue",
    steps,
    answer: "",
    content: [],
    pendingConsent: null,
    blocks: { tables: [], charts: [], citations: [] },
    state: "done",
  };
}

describe("artifact SQL provenance", () => {
  it("uses the matching table tool call", () => {
    const item: SavableContent = {
      id: "table-1",
      index: 1,
      type: "table",
      complete: true,
      block: { tool_call_id: "query-1", columns: ["n"], rows: [[1]] },
    };
    const turns = [
      turn("turn-1", [
        {
          id: "query-1",
          kind: "tool",
          label: "query_execute",
          text: "Ran a query",
          status: "done",
          preview: "SELECT COUNT(*) AS n FROM orders",
        },
      ]),
    ];
    expect(findArtifactSql(turns, "turn-1", item)).toBe(
      "SELECT COUNT(*) AS n FROM orders",
    );
  });

  it("uses the query recorded for a visual inside an interactive answer", () => {
    const request = {
      kind: "table" as const, title: "Outlets", evidenceId: "evidence_2",
      columns: ["outlet"], toolCallId: "query-2",
    };
    const item: SavableContent = {
      id: uiNodeContentId("content-3", request),
      type: "ui-node",
      request,
      block: { tool_call_id: request.toolCallId },
    };
    const step = (id: string, preview: string) => ({
      id, kind: "tool" as const, label: "query_execute", text: "Ran a query",
      status: "done" as const, preview,
    });
    const turns = [
      turn("turn-1", [step("query-1", "SELECT 1 AS n"), step("query-2", "SELECT outlet FROM outlets")]),
    ];
    expect(item.id).toContain("content-3:evidence_2:table:");
    expect(findArtifactSql(turns, "turn-1", item)).toBe("SELECT outlet FROM outlets");
    expect(findArtifactSql(turns, "turn-1", { ...item, block: {} })).toBeNull();
    expect(findArtifactSql(turns, "turn-1", {
      ...item, block: { tool_call_id: "missing-query" },
    })).toBeNull();
  });

  it("tracks distinct charts of the same result independently", () => {
    const request = {
      kind: "chart" as const, title: "Revenue", evidenceId: "e1", columns: ["month", "revenue"],
      chart: { type: "bar", x: "month", y: "revenue", series: [] },
    };
    expect(uiNodeContentId("ui", request)).not.toBe(uiNodeContentId("ui", {
      ...request, chart: { ...request.chart, type: "line" },
    }));
  });

  it("finds the previous query for a follow-up chart", () => {
    const item: SavableContent = {
      id: "chart-1",
      index: 0,
      type: "chart",
      complete: true,
      block: { tool_call_id: "chart-tool", chart_spec: "{}" },
    };
    const turns = [
      turn("turn-1", [
        {
          id: "query-1",
          kind: "tool",
          label: "semantic_query",
          text: "Retrieved data",
          status: "done",
          preview: "SELECT category, SUM(revenue) FROM sales GROUP BY category",
        },
      ]),
      turn("turn-2", [
        {
          id: "chart-tool",
          kind: "tool",
          label: "data_to_chart",
          text: "Built a chart",
          status: "done",
          preview: "data_to_chart: make it a bar",
        },
        {
          id: "later-query",
          kind: "tool",
          label: "query_execute",
          text: "Ran another query",
          status: "done",
          preview: "SELECT * FROM unrelated_table",
        },
      ]),
    ];
    expect(findArtifactSql(turns, "turn-2", item)).toBe(
      "SELECT category, SUM(revenue) FROM sales GROUP BY category",
    );
  });
});

describe("artifact SQL presentation", () => {
  it("formats clauses onto readable lines", () => {
    const formatted = formatArtifactSql(
      "select c.customer_id, sum(o.total_amount) as revenue from orders o join customers c on o.customer_id = c.customer_id group by c.customer_id order by revenue desc",
    );

    expect(formatted).toContain("SELECT");
    expect(formatted).toContain("\nFROM\n  orders o");
    expect(formatted).toContain("\n  JOIN customers c");
    expect(formatted).toContain("\nGROUP BY");
    expect(formatted).toContain("\nORDER BY");
  });
});
