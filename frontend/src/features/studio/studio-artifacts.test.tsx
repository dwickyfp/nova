import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StudioArtifacts } from "./studio-artifacts";

const mocks = vi.hoisted(() => ({
  listArtifacts: vi.fn(),
  getArtifact: vi.fn(),
  refreshArtifact: vi.fn(),
  editArtifact: vi.fn(),
  applyArtifactEdit: vi.fn(),
  deleteArtifact: vi.fn(),
  discardArtifactDraft: vi.fn(),
}));

const artifactMeta = {
  artifact_id: "artifact-1",
  title: "Revenue by region",
  artifact_type: "chart",
  agent_id: "agent-1",
  thread_id: "thread-1",
  database_name: "sales",
  schema_name: "analytics",
  sql_text: "SELECT region, revenue FROM sales.analytics.summary",
  chart_spec: null,
  viz_spec: null,
  created_at: "2026-09-21T08:00:00",
  updated_at: "2026-09-21T08:00:00",
};

vi.mock("@/features/agents/api", () => ({
  studioApi: mocks,
}));
const biSession = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api-client")>()),
  api: biSession,
}));

describe("Studio artifacts", () => {
  beforeEach(() => {
    mocks.listArtifacts.mockReset();
    mocks.getArtifact.mockReset();
    mocks.refreshArtifact.mockReset();
    mocks.editArtifact.mockReset();
    mocks.applyArtifactEdit.mockReset();
    mocks.deleteArtifact.mockReset();
    mocks.discardArtifactDraft.mockReset();
    mocks.discardArtifactDraft.mockResolvedValue(undefined);
    mocks.getArtifact.mockResolvedValue(artifactMeta);
    mocks.listArtifacts.mockResolvedValue({
      count: 1,
      artifacts: [
        {
          artifact_id: "artifact-1",
          title: "Revenue by region",
          artifact_type: "chart",
          agent_id: "agent-1",
          thread_id: "thread-1",
          database_name: "sales",
          schema_name: "analytics",
          created_at: "2026-09-21T08:00:00",
          updated_at: "2026-09-21T08:00:00",
        },
      ],
    });
    mocks.refreshArtifact.mockResolvedValue({
      artifact: {
        artifact_id: "artifact-1",
        title: "Revenue by region",
        artifact_type: "chart",
        agent_id: "agent-1",
        thread_id: "thread-1",
        database_name: "sales",
        schema_name: "analytics",
        sql_text: "SELECT region, revenue FROM sales.analytics.summary",
        chart_spec: null,
        created_at: "2026-09-21T08:00:00",
        updated_at: "2026-09-21T08:00:00",
      },
      columns: ["region", "revenue"],
      rows: [["West", 120]],
      row_count: 1,
      elapsed_ms: 8,
    });
  });

  it("opens fresh data and exposes Table and SQL views", async () => {
    const screen = await render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <StudioArtifacts onSelectAgent={() => undefined} />
      </QueryClientProvider>,
    );

    await expect
      .element(screen.getByText("Revenue by region"))
      .toBeInTheDocument();
    // The explorer card refreshes the sheet for its thumbnail.
    await expect
      .element(screen.getByRole("button", { name: "Open Revenue by region" }))
      .toBeInTheDocument();

    await userEvent.click(screen.getByText("Revenue by region"));
    await expect
      .element(screen.getByRole("tab", { name: "Table" }))
      .toBeInTheDocument();
    await expect.element(screen.getByText("West").first()).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "SQL" }));
    await expect
      .element(
        screen.getByText("SELECT region, revenue FROM sales.analytics.summary"),
      )
      .toBeInTheDocument();

    expect(mocks.refreshArtifact.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("applies a filter to both the artifact preview and data table", async () => {
    const screen = await render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <StudioArtifacts onSelectAgent={() => undefined} />
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByText("Revenue by region"));
    await userEvent.click(screen.getByRole("button", { name: "Add filter" }));
    await userEvent.fill(
      screen.getByRole("textbox", { name: "Filter value" }),
      "East",
    );
    await expect
      .element(screen.getByText("Showing 0 of 1 loaded rows"))
      .toBeInTheDocument();
    await expect
      .element(screen.getByText("No rows match these filters.").first())
      .toBeInTheDocument();

    await userEvent.fill(
      screen.getByRole("textbox", { name: "Filter value" }),
      "west",
    );
    await expect
      .element(screen.getByText("Showing 1 of 1 loaded rows"))
      .toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Clear filters" }),
    );
    await expect.element(screen.getByText("West").first()).toBeInTheDocument();
  });

  it("previews a chat edit and saves it only after Apply", async () => {
    mocks.editArtifact.mockResolvedValue({
      message: "Added profit to the table.",
      draft: {
        sql_text: "SELECT region, revenue, profit FROM sales.analytics.summary",
        artifact_type: "table",
        chart_spec: null,
      },
      columns: ["region", "revenue", "profit"],
      rows: [["West", 120, 35]],
      row_count: 1,
      elapsed_ms: 10,
    });
    mocks.applyArtifactEdit.mockResolvedValue({
      artifact: {
        artifact_id: "artifact-1",
        title: "Revenue by region",
        artifact_type: "table",
        agent_id: "agent-1",
        thread_id: "thread-1",
        database_name: "sales",
        schema_name: "analytics",
        sql_text: "SELECT region, revenue, profit FROM sales.analytics.summary",
        chart_spec: null,
        created_at: "2026-09-21T08:00:00",
        updated_at: "2026-09-21T09:00:00",
      },
      columns: ["region", "revenue", "profit"],
      rows: [["West", 120, 35]],
      row_count: 1,
      elapsed_ms: 10,
    });
    const screen = await render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <StudioArtifacts onSelectAgent={() => undefined} />
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByText("Revenue by region"));
    await userEvent.fill(
      screen.getByRole("textbox", { name: "Edit artifact with Nova" }),
      "Add profit",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Send artifact edit" }),
    );
    await expect
      .element(screen.getByRole("group", { name: "Unsaved change" }))
      .toBeInTheDocument();
    await expect
      .element(screen.getByText("profit").first())
      .toBeInTheDocument();
    expect(mocks.applyArtifactEdit).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "Apply changes" }),
    );
    await expect
      .element(screen.getByRole("group", { name: "Unsaved change" }))
      .not.toBeInTheDocument();
    expect(mocks.applyArtifactEdit).toHaveBeenCalledWith("artifact-1", {
      draft: {
        sql_text: "SELECT region, revenue, profit FROM sales.analytics.summary",
        artifact_type: "table",
        chart_spec: null,
      },
      expected_updated_at: "2026-09-21T08:00:00",
    });
  });

  it("shows what Nova is changing while the chart rests, without chat bubbles", async () => {
    let finishEdit: (value: unknown) => void = () => undefined;
    mocks.editArtifact.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishEdit = resolve;
        }),
    );
    const screen = await render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <StudioArtifacts onSelectAgent={() => undefined} />
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByText("Revenue by region"));
    await userEvent.fill(
      screen.getByRole("textbox", { name: "Edit artifact with Nova" }),
      "Rename region",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Send artifact edit" }),
    );
    const preview = screen.getByRole("region", { name: "Artifact preview" });
    await expect.element(preview).toHaveAttribute("aria-busy", "true");
    await expect
      .element(screen.getByText("Changing the chart: “Rename region”"))
      .toBeInTheDocument();
    await expect
      .element(screen.getByRole("region", { name: "Artifact chat" }))
      .not.toBeInTheDocument();

    finishEdit({
      message: "Which source column should I rename?",
      draft: null,
      columns: [],
      rows: [],
      row_count: 0,
      elapsed_ms: 0,
    });
    await expect.element(preview).toHaveAttribute("aria-busy", "false");
    await expect
      .element(screen.getByText("Changing the chart: “Rename region”"))
      .not.toBeInTheDocument();
    await expect
      .element(screen.getByText("Which source column should I rename?"))
      .toBeInTheDocument();
  });

  it("draws a BI artifact in the panel and edits it edge to edge", async () => {
    biSession.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    const bi = {
      bi_kind: "chart",
      bi_object_id: 21,
      bi_view_path: "/bi/explore/?slice_id=21&standalone=1",
      bi_edit_path: "/bi/explore/?slice_id=21&uiConfig=4",
    };
    mocks.getArtifact.mockResolvedValue({ ...artifactMeta, ...bi });
    const refreshed = await mocks.refreshArtifact();
    mocks.refreshArtifact.mockResolvedValue({
      ...refreshed,
      artifact: { ...refreshed.artifact, ...bi },
    });
    const screen = await render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <StudioArtifacts onSelectAgent={() => undefined} />
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByText("Revenue by region"));

    await expect
      .element(screen.getByTitle("Chart Revenue by region"))
      .toHaveAttribute("src", "/bi/explore/?slice_id=21&standalone=1");
    await userEvent.click(screen.getByRole("button", { name: "Edit chart" }));
    await expect
      .element(screen.getByTitle("Edit Revenue by region"))
      .toHaveAttribute("src", "/bi/explore/?slice_id=21&uiConfig=4");
    const editor = screen.getByRole("region", { name: "Chart editor" });
    await expect.element(editor).not.toHaveClass("rounded-xl");
    expect(editor.element().parentElement?.className).not.toContain("overflow-y-auto");
    // The editor shows the title and owns Save; Nova's bar only leaves it.
    await expect
      .element(screen.getByRole("heading", { name: "Revenue by region" }))
      .not.toBeInTheDocument();
    await expect
      .element(screen.getByRole("textbox", { name: "Edit artifact with Nova" }))
      .toHaveAttribute("rows", "1");
    await userEvent.click(screen.getByRole("button", { name: "Done editing" }));
    await expect
      .element(screen.getByTitle("Chart Revenue by region"))
      .toHaveAttribute("src", "/bi/explore/?slice_id=21&standalone=1");
    await expect
      .element(screen.getByRole("heading", { name: "Revenue by region" }))
      .toBeInTheDocument();
    expect(biSession.post).toHaveBeenCalledWith(
      "/superset/session",
      undefined,
      expect.any(AbortSignal),
    );
  });

  it("previews a Nova edit made while editing in BI, then reopens BI", async () => {
    biSession.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    const bi = {
      bi_kind: "chart",
      bi_object_id: 21,
      bi_view_path: "/bi/explore/?slice_id=21&standalone=1",
      bi_edit_path: "/bi/explore/?slice_id=21&uiConfig=4",
    };
    mocks.getArtifact.mockResolvedValue({ ...artifactMeta, ...bi });
    const refreshed = await mocks.refreshArtifact();
    mocks.refreshArtifact.mockResolvedValue({
      ...refreshed,
      artifact: { ...refreshed.artifact, ...bi },
    });
    mocks.editArtifact.mockResolvedValue({
      message: "Made it a bar chart.",
      draft: { sql_text: artifactMeta.sql_text, artifact_type: "chart", chart_spec: null },
      columns: ["region", "revenue"],
      rows: [["West", 120]],
      row_count: 1,
      elapsed_ms: 10,
    });
    mocks.applyArtifactEdit.mockResolvedValue({
      ...refreshed,
      artifact: { ...refreshed.artifact, ...bi, updated_at: "2026-09-21T09:00:00" },
    });
    const screen = await render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <StudioArtifacts onSelectAgent={() => undefined} />
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByText("Revenue by region"));
    await userEvent.click(screen.getByRole("button", { name: "Edit chart" }));
    await userEvent.fill(
      screen.getByRole("textbox", { name: "Edit artifact with Nova" }),
      "Make it a bar chart",
    );
    await userEvent.click(screen.getByRole("button", { name: "Send artifact edit" }));

    await expect
      .element(screen.getByRole("group", { name: "Unsaved change" }))
      .toBeInTheDocument();
    await expect
      .element(screen.getByTitle("Edit Revenue by region"))
      .not.toBeInTheDocument();
    await expect
      .element(screen.getByRole("region", { name: "Artifact preview" }))
      .toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Apply changes" }));
    await expect
      .element(screen.getByTitle("Edit Revenue by region"))
      .toHaveAttribute("src", "/bi/explore/?slice_id=21&uiConfig=4");
  });

  it("views a dashboard without an empty filter bar and edits it under one header", async () => {
    biSession.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    const dashboard = {
      ...artifactMeta,
      artifact_id: "dash-1",
      title: "Sales overview",
      artifact_type: "dashboard",
      sql_text: "",
      bi_kind: "dashboard",
      bi_object_id: 7,
      bi_view_path: "/bi/superset/dashboard/7/?standalone=2",
      bi_edit_path: "/bi/superset/dashboard/7/?edit=true&standalone=1",
    };
    mocks.listArtifacts.mockResolvedValue({ count: 1, artifacts: [dashboard] });
    mocks.getArtifact.mockResolvedValue(dashboard);
    const fetchMock = vi.spyOn(window, "fetch").mockImplementation(async (input) =>
      String(input) === "/bi/api/v1/dashboard/7"
        ? new Response(JSON.stringify({ result: { json_metadata: "{}" } }), { status: 200 })
        : new Response(null, { status: 404 }),
    );
    try {
      const screen = await render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <StudioArtifacts onSelectAgent={() => undefined} />
        </QueryClientProvider>,
      );
      await userEvent.click(screen.getByRole("button", { name: "Open Sales overview" }));
      await expect
        .element(screen.getByTitle("Dashboard Sales overview"))
        .toHaveAttribute("src", "/bi/superset/dashboard/7/?standalone=2&show_filters=0");

      // The editor keeps the filter bar, where filters are added, and shows
      // the title itself: Nova's bar only leaves it.
      await userEvent.click(screen.getByRole("button", { name: "Edit dashboard" }));
      await expect
        .element(screen.getByTitle("Edit Sales overview"))
        .toHaveAttribute("src", "/bi/superset/dashboard/7/?edit=true&standalone=1");
      await expect
        .element(screen.getByRole("heading", { name: "Sales overview" }))
        .not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Done editing" }));
      await expect.element(screen.getByTitle("Dashboard Sales overview")).toBeInTheDocument();
    } finally {
      fetchMock.mockRestore();
    }
  });

  it("shows a BI draft in Superset's own frame and returns to the saved chart", async () => {
    biSession.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    const bi = {
      bi_kind: "chart",
      bi_object_id: 21,
      bi_view_path: "/bi/explore/?slice_id=21&standalone=1",
      bi_edit_path: "/bi/explore/?slice_id=21&uiConfig=4",
    };
    mocks.getArtifact.mockResolvedValue({ ...artifactMeta, ...bi });
    const refreshed = await mocks.refreshArtifact();
    mocks.refreshArtifact.mockResolvedValue({ ...refreshed, artifact: { ...refreshed.artifact, ...bi } });
    mocks.editArtifact.mockResolvedValue({
      message: "Made it a line chart, largest first.",
      draft: { sql_text: artifactMeta.sql_text, artifact_type: "chart", chart_spec: { mark: "line" } },
      columns: ["region", "revenue"],
      rows: [["West", 120]],
      row_count: 1,
      elapsed_ms: 10,
      preview_path: "/bi/explore/?form_data_key=K1&standalone=1",
    });
    const screen = await render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <StudioArtifacts onSelectAgent={() => undefined} />
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByText("Revenue by region"));
    await expect
      .element(screen.getByTitle("Chart Revenue by region"))
      .toHaveAttribute("src", "/bi/explore/?slice_id=21&standalone=1");
    await userEvent.fill(screen.getByRole("textbox", { name: "Edit artifact with Nova" }), "Line, largest first");
    await userEvent.click(screen.getByRole("button", { name: "Send artifact edit" }));

    // The draft is Superset's own view, in the same frame; nothing moves.
    await expect
      .element(screen.getByTitle("Draft of Revenue by region"))
      .toHaveAttribute("src", "/bi/explore/?form_data_key=K1&standalone=1");
    await expect
      .element(screen.getByText("Made it a line chart, largest first."))
      .toBeInTheDocument();
    await expect.element(screen.getByRole("button", { name: "Edit chart" })).toBeDisabled();
    await expect.element(screen.getByRole("button", { name: "Export" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(mocks.discardArtifactDraft).toHaveBeenCalledWith("artifact-1");
    await expect
      .element(screen.getByTitle("Chart Revenue by region"))
      .toHaveAttribute("src", "/bi/explore/?slice_id=21&standalone=1");
    await expect.element(screen.getByRole("button", { name: "Edit chart" })).toBeEnabled();
  });
});
