import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import contract from "../../../../../../backend/app/modules/assistant/ui_components.json";
import { parseComponentBlock } from "./block";
import { DatavizChart, KpiTiles } from "./registry";
import { componentContract } from "./schemas";
import "@/styles/index.css";

const DATASET = "dddddddd-0000-4000-8000-000000000001";
const REF = "dr1.eyJjIjoicnVuOjMiLCJ0IjoidDEifQ.c2lnbmF0dXJlLXNpZ25hdHVyZQ";
const chart = {
  schema_version: 2,
  chart_id: "cccccccc-0000-4000-8000-000000000001",
  title: "Revenue by region",
  dataset_id: DATASET,
  dataset_revision: 1,
  visual: "bar",
  encoding: {
    kind: "cartesian",
    dimensions: [{ field_id: "region" }],
    measures: [{ field_id: "revenue", aggregation: "sum" }],
  },
};
const RESULT = {
  dataset_id: DATASET,
  revision: 1,
  truncated: false,
  columns: [
    { key: "d0", name: "region", logical_type: "string", role: "dimension", field_id: "region" },
    {
      key: "m0", name: "revenue", logical_type: "decimal", role: "measure",
      field_id: "revenue", aggregation: "sum",
    },
  ],
  rows: [
    [{ type: "string", value: "Jakarta" }, { type: "decimal", value: "979755000.00" }],
    [{ type: "string", value: "Medan" }, { type: "decimal", value: "507710000.00" }],
  ],
};

function respond(status: number, body: unknown) {
  const fetch = vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function show(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <div style={{ width: 560 }}>{node}</div>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("DatavizChart", () => {
  it("draws the native chart from its draft query, as the viewer", async () => {
    const fetch = respond(200, RESULT);
    const screen = await show(
      <DatavizChart props={{ title: "Revenue by region", chart, draft_ref: REF }} ready />,
    );
    await expect.element(screen.getByRole("heading", { name: "Revenue by region" })).toBeVisible();
    await vi.waitFor(() => expect(screen.container.querySelector("canvas")).not.toBeNull(),
      { timeout: 10_000 });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain(`/agents/studio/dataviz/drafts/${encodeURIComponent(REF)}/query`);
    const body = JSON.parse(String(init.body));
    expect(body.revision).toBe(1);
    expect(body.dimensions).toMatchObject([{ field_id: "region" }]);
    // The client never sends a statement; the server reads it from its own record.
    expect(String(init.body)).not.toMatch(/select/i);
  });

  it("holds its box and reads nothing until the turn is stored", async () => {
    const fetch = respond(200, RESULT);
    const screen = await show(
      <DatavizChart props={{ title: "Revenue by region", chart, draft_ref: REF }} ready={false} />,
    );
    await expect.element(screen.getByRole("status", { name: "Preparing Revenue by region" })).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("explains a refused draft in the server's own plain words", async () => {
    respond(409, {
      detail: "This chart was made under another role or session. Ask again under your current role to see it.",
    });
    const screen = await show(
      <DatavizChart props={{ title: "Revenue by region", chart, draft_ref: REF }} ready />,
    );
    await expect.element(screen.getByRole("alert")).toHaveTextContent("made under another role");
    expect(screen.container.querySelector("canvas")).toBeNull();
  });

  it("does not guess a chart it cannot read", async () => {
    const fetch = respond(200, RESULT);
    const screen = await show(
      <DatavizChart props={{ title: "Broken", chart: { visual: "bar" }, draft_ref: REF }} ready />,
    );
    await expect.element(screen.getByText("This chart cannot be shown.")).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("KpiTiles", () => {
  it("shows each verified value formatted, with the exact value on hover", async () => {
    const screen = await show(
      <KpiTiles
        props={{
          items: [
            { label: "Revenue", value: 356805054, format: "currency", currency: "IDR", decimals: 0,
              aggregation: "value", evidence_id: "e1", column: "revenue" },
            { label: "Orders", value: 1240, format: "number", decimals: 0,
              aggregation: "sum", evidence_id: "e1", column: "orders" },
            { label: "Margin", value: null, format: "percent",
              aggregation: "value", evidence_id: "e1", column: "margin" },
          ],
        }}
      />,
    );
    await expect.element(screen.getByText("Revenue")).toBeVisible();
    const values = [...screen.container.querySelectorAll("dd")];
    expect(values).toHaveLength(3);
    expect(values[0].getAttribute("title")).toMatch(/356[.,\s]?805[.,\s]?054/);
    expect(values[0].textContent).not.toBe(values[0].getAttribute("title"));
    expect(values[1].textContent).toMatch(/1[.,\s]?240/);
    expect(values[2].textContent).toBe("No value");
  });
});

describe("component blocks", () => {
  it("accepts a block that matches the registry and refuses everything else", () => {
    const kpi = {
      component: "KpiTiles",
      version: 1,
      props: { items: [{ label: "Revenue", value: 1, format: "number", aggregation: "value",
                          evidence_id: "e1", column: "revenue" }] },
    };
    expect(parseComponentBlock(kpi)?.component).toBe("KpiTiles");
    expect(parseComponentBlock({ ...kpi, version: 2 })).toBeNull();
    expect(parseComponentBlock({ ...kpi, component: "Chart" })).toBeNull();
    expect(parseComponentBlock({ ...kpi, component: "toString" })).toBeNull();
    expect(parseComponentBlock({ ...kpi, props: { items: [] } })).toBeNull();
    expect(
      parseComponentBlock({
        component: "DatavizChart", version: 1,
        props: { title: "R", chart, draft_ref: REF, rows: [] },
      }),
    ).toBeNull();
  });

  it("matches the contract the backend validates with", () => {
    expect(JSON.parse(JSON.stringify(componentContract()))).toEqual(contract);
  });
});
