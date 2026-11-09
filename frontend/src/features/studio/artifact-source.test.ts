import { describe, expect, it } from "vitest";
import { formatArtifactSql } from "./artifact-source";

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
