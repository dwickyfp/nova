import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import "@/styles/index.css";
import {
  SemanticResultTable,
  SemanticViewGlance,
  versionStatusWord,
} from "./semantic-view-glance";
import type { SemanticVersion, SemanticViewDetail } from "./semantic-views-api";

function version(status: string, number: number, definition: Record<string, unknown> = {}) {
  return { version: number, status, definition } as unknown as SemanticVersion;
}

const definition = {
  description: "Sales per order line.",
  metrics: [
    { name: "net_revenue", synonyms: ["omzet", "penjualan"] },
    { name: "units_sold" },
  ],
  datasets: [
    {
      fields: [
        { name: "order_id", dimension: true },
        { name: "order_date", dimension: true, datatype: "Date" },
        { name: "quantity", dimension: true, datatype: "Integer" },
        { name: "city", dimension: true },
      ],
    },
  ],
};

function view(versions: SemanticVersion[], active: number | null, status = "ACTIVE") {
  return { id: "sv1", name: "sales", status, active_version: active, versions } as unknown as SemanticViewDetail;
}

describe("Semantic View at a glance", () => {
  it("says what can be asked, without ids or raw numbers as breakdowns", async () => {
    const live = version("ACTIVE", 2, definition);
    const screen = await render(
      <SemanticViewGlance view={view([live], 2)} version={live} onNext={() => {}} />,
    );
    await expect.element(screen.getByText("Live · version 2")).toBeVisible();
    await expect.element(screen.getByText("net revenue", { exact: true })).toBeVisible();
    await expect.element(screen.getByText(/also “omzet”, “penjualan”/)).toBeVisible();
    await expect.element(screen.getByText("order date, city")).toBeVisible();
    await expect.element(screen.getByText("“net revenue by city last month”")).toBeVisible();
    expect(screen.getByText(/order id/).query()).toBeNull();
  });

  it("points a draft to its next step", async () => {
    const live = version("ACTIVE", 1, definition);
    const draft = version("DRAFT", 2, definition);
    const onNext = vi.fn();
    const screen = await render(
      <SemanticViewGlance view={view([draft, live], 1)} version={live} onNext={onNext} />,
    );
    await expect
      .element(screen.getByText("Version 2 is a draft. Check it, then make it live."))
      .toBeVisible();
    await screen.getByRole("button", { name: "Check the draft" }).click();
    expect(onNext).toHaveBeenCalledOnce();
  });

  it("names version states in words", () => {
    expect(versionStatusWord("ACTIVE")).toBe("Live");
    expect(versionStatusWord("VALIDATED")).toBe("Checked");
    expect(versionStatusWord("DEPRECATED")).toBe("Replaced");
    expect(versionStatusWord("DEPRECATED", true)).toBe("Retired");
  });
});

describe("Explore the numbers results", () => {
  it("shows a table with readable headers and one number format", async () => {
    const screen = await render(
      <SemanticResultTable
        result={{
          columns: ["outlets.city", "net_revenue"],
          rows: [
            ["Jakarta", 3102485958],
            ["Surabaya", "2235805129.028"],
          ],
        }}
      />,
    );
    await expect.element(screen.getByRole("columnheader", { name: "city" })).toBeVisible();
    await expect.element(screen.getByRole("columnheader", { name: "net revenue" })).toBeVisible();
    await expect.element(screen.getByText("3,102,485,958")).toBeVisible();
    await expect.element(screen.getByText("2,235,805,129.03")).toBeVisible();
  });
});
