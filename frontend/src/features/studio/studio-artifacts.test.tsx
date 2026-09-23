import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StudioArtifacts } from "./studio-artifacts";

const mocks = vi.hoisted(() => ({
  listArtifacts: vi.fn(),
  refreshArtifact: vi.fn(),
  editArtifact: vi.fn(),
  applyArtifactEdit: vi.fn(),
  deleteArtifact: vi.fn(),
}));

vi.mock("@/features/agents/api", () => ({
  studioApi: mocks,
}));

describe("Studio artifacts", () => {
  beforeEach(() => {
    mocks.listArtifacts.mockReset();
    mocks.refreshArtifact.mockReset();
    mocks.editArtifact.mockReset();
    mocks.applyArtifactEdit.mockReset();
    mocks.deleteArtifact.mockReset();
    mocks.listArtifacts.mockResolvedValue({
      count: 1,
      artifacts: [
        {
          artifact_id: "artifact-1",
          title: "Revenue by region",
          artifact_type: "table",
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
        artifact_type: "table",
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
    await expect.element(screen.getByText("West")).toBeInTheDocument();

    await userEvent.click(screen.getByText("Revenue by region"));
    await expect
      .element(screen.getByRole("tab", { name: "Table" }))
      .toBeInTheDocument();
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
    await expect.element(screen.getByText("Draft preview")).toBeInTheDocument();
    await expect
      .element(screen.getByText("profit").first())
      .toBeInTheDocument();
    expect(mocks.applyArtifactEdit).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "Apply changes" }),
    );
    await expect
      .element(screen.getByText("Draft preview"))
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

  it("shows activity on the preview border without rendering chat bubbles", async () => {
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
    await expect.element(preview).toHaveClass("artifact-ai-active");
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
    await expect.element(preview).not.toHaveClass("artifact-ai-active");
    await expect
      .element(screen.getByText("Which source column should I rename?"))
      .toBeInTheDocument();
  });
});
