import { describe, expect, it } from "vitest";
import { render } from "vitest-browser-react";
import { SqlCodeBlock } from "./sql-code-block";
import { highlightExplorerSql } from "./sql-highlighter";

describe("SQL code block", () => {
  it("highlights SQL keywords, types, numbers, and quoted identifiers", () => {
    const html = highlightExplorerSql(
      "CREATE TABLE `monthly_revenue` (`record_id` bigint(20) NOT NULL)",
    );

    expect(html).toContain("hljs-keyword");
    expect(html).toContain("hljs-type");
    expect(html).toContain("hljs-number");
    expect(html).toContain('<span class="hljs-attr">`monthly_revenue`</span>');
    expect(html).toContain('<span class="hljs-attr">`record_id`</span>');
  });

  it("escapes markup before adding identifier colors", () => {
    const html = highlightExplorerSql("SELECT `<img src=x onerror=alert(1)>`");

    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;");
    expect(html).toContain("&gt;");
  });

  it("keeps the complete SQL readable in the rendered code block", async () => {
    const source = "SELECT `report_month` FROM `monthly_revenue`";
    const { container } = await render(
      <SqlCodeBlock source={source} label="Table DDL" />,
    );
    const block = (container as HTMLElement).querySelector("pre");

    expect(block?.getAttribute("aria-label")).toBe(
      "Table DDL syntax highlighted code",
    );
    expect(block?.textContent).toBe(source);
    expect(block?.querySelectorAll(".hljs-attr")).toHaveLength(2);
  });
});
