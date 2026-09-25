import { beforeEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import "@/styles/index.css";
import { MigrationPage } from "./index";

const apiMocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("@/lib/api-client", () => ({ api: apiMocks }));
vi.mock("@/components/layout/header", () => ({
  Header: ({ children }: { children: React.ReactNode }) => (
    <header>{children}</header>
  ),
}));

const source = {
  id: "source-1",
  name: "origin",
  host: "10.0.0.12",
  port: 9030,
  username: "operator",
  secret_ref: "",
  comment: "",
  created_at: null,
  created_by: null,
};

beforeEach(() => {
  vi.resetAllMocks();
  window.sessionStorage.clear();
  apiMocks.get.mockImplementation(async (path: string) => {
    if (path === "/migration/capabilities")
      return {
        phase: "11",
        version: "v1",
        phases: [],
        write_operations: true,
        execute_available: true,
        execute_gate: { issue: "#7", name: "backup/restore" },
        mv_ddl_surface: "",
      };
    if (path === "/migration/engine")
      return {
        available: true,
        configured_path: null,
        resolved_path: null,
        reason: null,
      };
    if (path === "/migration/sources") return { connections: [], count: 0 };
    if (path === "/migration/jobs/job-1")
      return {
        job_id: "job-1",
        source: "origin",
        databases: ["analytics", "sales"],
        status: "succeeded",
        current_database: null,
        results: [
          {
            database: "analytics",
            status: "succeeded",
            succeeded: 2,
            failed: 0,
            skipped: 0,
            rows_moved: 0,
            error: null,
            steps: [
              {
                order: 1,
                kind: "database",
                object_name: "analytics",
                status: "ok",
                error: null,
              },
            ],
            data: [],
          },
          {
            database: "sales",
            status: "succeeded",
            succeeded: 2,
            failed: 0,
            skipped: 0,
            rows_moved: 0,
            error: null,
            steps: [],
            data: [],
          },
        ],
        created_at: "2026-09-24T00:00:00Z",
        started_at: null,
        finished_at: null,
      };
    throw new Error(`Unexpected GET ${path}`);
  });
  apiMocks.post.mockImplementation(
    async (path: string, body: Record<string, unknown>) => {
      if (path === "/migration/sources/test") return { connected: true };
      if (path === "/migration/sources") return { ...source, ...body };
      if (path === "/migration/databases")
        return {
          source: body.source,
          databases: ["sales", "analytics"],
          count: 2,
          unsupported: ["sales archive"],
        };
      if (path === "/migration/dry-run")
        return {
          database: body.database,
          items: [],
          summary: { migratable: 2, lossy: 0, skipped: 0 },
          engine_available: true,
          engine_path: null,
        };
      if (path === "/migration/plan")
        return {
          source_database: body.database,
          target_database: body.database,
          steps: [
            {
              order: 1,
              kind: "database",
              object_name: body.database,
              statement: `CREATE DATABASE ${body.database}`,
              dropped_properties: [],
            },
          ],
          blocked: [],
          step_count: 1,
          execute_available: true,
        };
      if (path === "/migration/preflight")
        return {
          source_database: body.database,
          target_database: body.database,
          database_step_planned: true,
          checks: [],
          missing: [],
          storage_checked: false,
          storage_ok: null,
          storage_reason: "",
          ok: true,
        };
      if (path === "/migration/execute-batch")
        return {
          job_id: "job-1",
          status: "queued",
          source: body.source,
          databases: body.databases,
        };
      throw new Error(`Unexpected POST ${path}`);
    },
  );
});

async function registerAndDiscover() {
  const screen = await render(<MigrationPage />);
  await expect
    .element(
      screen.getByRole("button", { name: "Save source and list databases" }),
    )
    .toBeVisible();
  await userEvent.fill(
    screen.getByRole("textbox", { name: "Connection name" }),
    "origin",
  );
  await userEvent.fill(
    screen.getByRole("textbox", { name: "IP or hostname" }),
    "10.0.0.12",
  );
  await userEvent.fill(
    screen.getByRole("textbox", { name: "Username" }),
    "operator",
  );
  await expect
    .element(
      screen.getByRole("button", { name: "Save source and list databases" }),
    )
    .toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
  await expect
    .element(screen.getByText("Connection successful. You can save this source."))
    .toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "Save source and list databases" }),
  );
  await expect
    .element(screen.getByRole("checkbox", { name: "analytics" }))
    .toBeVisible();
  return screen;
}

describe("MigrationPage", () => {
  it("allows connection testing while migration execution is disabled", async () => {
    const normalGet = apiMocks.get.getMockImplementation();
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === "/migration/capabilities") {
        return {
          phase: "11",
          version: "v1",
          phases: [],
          write_operations: false,
          execute_available: false,
          execute_gate: { issue: "#7", name: "backup/restore" },
          mv_ddl_surface: "",
        };
      }
      return normalGet?.(path);
    });
    const screen = await render(<MigrationPage />);
    await expect
      .element(screen.getByText(/MIGRATION_EXECUTE_ENABLED=true/))
      .toBeVisible();
    await userEvent.fill(
      screen.getByRole("textbox", { name: "Connection name" }),
      "origin",
    );
    await userEvent.fill(
      screen.getByRole("textbox", { name: "IP or hostname" }),
      "10.0.0.12",
    );
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await expect
      .element(screen.getByText("Connection successful. You can save this source."))
      .toBeVisible();
  });

  it("requires a successful connection test and invalidates it when the address changes", async () => {
    const normalPost = apiMocks.post.getMockImplementation();
    let failProbe = true;
    apiMocks.post.mockImplementation(
      async (path: string, body: Record<string, unknown>) => {
        if (path === "/migration/sources/test" && failProbe) {
          throw new Error("source refused");
        }
        return normalPost?.(path, body);
      },
    );
    const screen = await render(<MigrationPage />);
    await userEvent.fill(
      screen.getByRole("textbox", { name: "Connection name" }),
      "origin",
    );
    await userEvent.fill(
      screen.getByRole("textbox", { name: "IP or hostname" }),
      "10.0.0.12",
    );
    const save = screen.getByRole("button", {
      name: "Save source and list databases",
    });
    await expect.element(save).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await expect
      .element(screen.getByText(/Could not test this connection/))
      .toBeVisible();
    await expect.element(save).toBeDisabled();
    expect(apiMocks.post).not.toHaveBeenCalledWith(
      "/migration/sources",
      expect.anything(),
    );

    failProbe = false;
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await expect.element(save).toBeEnabled();
    await userEvent.fill(
      screen.getByRole("textbox", { name: "IP or hostname" }),
      "10.0.0.13",
    );
    await expect.element(save).toBeDisabled();
    await expect
      .element(screen.getByText("Connection successful. You can save this source."))
      .not.toBeInTheDocument();
  });

  it("discovers databases only after source details are saved and supports multi-selection", async () => {
    const screen = await registerAndDiscover();
    expect(apiMocks.post).toHaveBeenCalledWith(
      "/migration/sources/test",
      expect.objectContaining({
        source: "origin",
        host: "10.0.0.12",
        port: 9030,
        username: "operator",
      }),
    );
    expect(apiMocks.post).toHaveBeenCalledWith(
      "/migration/sources",
      expect.objectContaining({
        name: "origin",
        host: "10.0.0.12",
        port: 9030,
        username: "operator",
      }),
    );
    expect(apiMocks.post).toHaveBeenCalledWith("/migration/databases", {
      source: "origin",
    });
    await userEvent.click(screen.getByRole("checkbox", { name: "analytics" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "sales" }));
    await expect
      .element(screen.getByText("2 of 50 selected", { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByText("sales archive", { exact: true }))
      .toBeVisible();
    await expect
      .element(
        screen.getByRole("button", { name: "Review 2 selected databases" }),
      )
      .toBeEnabled();
  });

  it("reviews every selected database before each worker job", async () => {
    const screen = await registerAndDiscover();
    await userEvent.click(screen.getByRole("checkbox", { name: "analytics" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "sales" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Review 2 selected databases" }),
    );
    await expect
      .element(screen.getByText("Type MIGRATE 2 DATABASES to confirm"))
      .toBeVisible();
    expect(apiMocks.post).toHaveBeenCalledWith("/migration/plan", {
      source: "origin",
      database: "analytics",
    });
    expect(apiMocks.post).toHaveBeenCalledWith("/migration/plan", {
      source: "origin",
      database: "sales",
    });
    await userEvent.click(
      screen.getByRole("checkbox", { name: /I reviewed every plan/ }),
    );
    await userEvent.fill(
      screen.getByRole("textbox", {
        name: "Type MIGRATE 2 DATABASES to confirm",
      }),
      "MIGRATE 2 DATABASES",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Queue migration job" }),
    );
    await expect.element(screen.getByText("job-1")).toBeVisible();
    expect(apiMocks.post).toHaveBeenCalledWith("/migration/execute-batch", {
      source: "origin",
      databases: ["analytics", "sales"],
      acknowledge_omissions: true,
      confirmation: "MIGRATE 2 DATABASES",
      include_data: false,
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Refresh job status" }),
    );
    await expect
      .element(screen.getByText("succeeded", { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByRole("button", { name: "Queue migration job" }))
      .toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "Review 2 selected databases" }),
    );
    await userEvent.click(
      screen.getByRole("checkbox", { name: /I reviewed every plan/ }),
    );
    await userEvent.fill(
      screen.getByRole("textbox", {
        name: "Type MIGRATE 2 DATABASES to confirm",
      }),
      "MIGRATE 2 DATABASES",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Queue migration job" }),
    );
    expect(
      apiMocks.post.mock.calls.filter(
        ([path]) => path === "/migration/execute-batch",
      ),
    ).toHaveLength(2);
  });

  it("invalidates the review when the selection changes", async () => {
    const screen = await registerAndDiscover();
    await userEvent.click(screen.getByRole("checkbox", { name: "analytics" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Review 1 selected database" }),
    );
    await expect
      .element(screen.getByText("Type MIGRATE 1 DATABASES to confirm"))
      .toBeVisible();
    await userEvent.click(screen.getByRole("checkbox", { name: "sales" }));
    await expect
      .element(screen.getByText("Type MIGRATE 1 DATABASES to confirm"))
      .not.toBeInTheDocument();
    expect(apiMocks.post).not.toHaveBeenCalledWith(
      "/migration/execute-batch",
      expect.anything(),
    );
  });

  it("blocks submission when a selected database fails preflight", async () => {
    const normalPost = apiMocks.post.getMockImplementation();
    apiMocks.post.mockImplementation(
      async (path: string, body: Record<string, unknown>) => {
        if (path === "/migration/preflight" && body.database === "sales") {
          return {
            source_database: "sales",
            target_database: "sales",
            database_step_planned: true,
            checks: [
              {
                privilege: "CREATE DATABASE",
                reason: "Create the target database",
                satisfied: false,
              },
            ],
            missing: ["CREATE DATABASE"],
            storage_checked: false,
            storage_ok: null,
            storage_reason: "",
            ok: false,
          };
        }
        return normalPost?.(path, body);
      },
    );
    const screen = await registerAndDiscover();
    await userEvent.click(screen.getByRole("button", { name: "Select shown" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Review 2 selected databases" }),
    );
    await expect.element(screen.getByText("Preflight failed")).toBeVisible();
    await expect
      .element(screen.getByRole("button", { name: "Queue migration job" }))
      .toBeDisabled();
    expect(apiMocks.post).not.toHaveBeenCalledWith(
      "/migration/execute-batch",
      expect.anything(),
    );
  });

  it("shows a retry after database discovery fails", async () => {
    const normalPost = apiMocks.post.getMockImplementation();
    let attempts = 0;
    apiMocks.post.mockImplementation(
      async (path: string, body: Record<string, unknown>) => {
        if (path === "/migration/databases" && attempts++ === 0) {
          throw new Error("Source connection refused");
        }
        return normalPost?.(path, body);
      },
    );
    const screen = await render(<MigrationPage />);
    await userEvent.fill(
      screen.getByRole("textbox", { name: "Connection name" }),
      "origin",
    );
    await userEvent.fill(
      screen.getByRole("textbox", { name: "IP or hostname" }),
      "10.0.0.12",
    );
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await expect
      .element(screen.getByText("Connection successful. You can save this source."))
      .toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Save source and list databases" }),
    );
    await expect
      .element(screen.getByText("Source connection refused"))
      .toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await expect
      .element(screen.getByRole("checkbox", { name: "analytics" }))
      .toBeVisible();
  });

  it("restores a submitted job after reopening the page", async () => {
    window.sessionStorage.setItem("nova.migration.lastJobId", "job-1");
    const screen = await render(<MigrationPage />);
    await expect.element(screen.getByText("job-1")).toBeVisible();
    await expect
      .element(screen.getByText("View results for analytics"))
      .toBeVisible();
    expect(apiMocks.get).toHaveBeenCalledWith("/migration/jobs/job-1");
  });

  it("explains a worker failure before any database result is recorded", async () => {
    const normalGet = apiMocks.get.getMockImplementation();
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === "/migration/jobs/job-1") {
        return {
          job_id: "job-1",
          source: "origin",
          databases: ["analytics"],
          status: "failed",
          current_database: null,
          results: [],
          error_code: "session_expired",
          created_at: null,
        };
      }
      return normalGet?.(path);
    });
    window.sessionStorage.setItem("nova.migration.lastJobId", "job-1");
    const screen = await render(<MigrationPage />);
    await expect
      .element(screen.getByText(/The migration session expired/))
      .toBeVisible();
    await expect.element(screen.getByText("Not completed")).toBeVisible();
    await expect
      .element(screen.getByText("session_expired"))
      .not.toBeInTheDocument();
  });

  it("uses a generic message for an unknown worker error code", async () => {
    const normalGet = apiMocks.get.getMockImplementation();
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === "/migration/jobs/job-1") {
        return {
          job_id: "job-1",
          source: "origin",
          databases: ["analytics"],
          status: "failed",
          current_database: null,
          results: [],
          error_code: "unexpected_internal_detail",
          created_at: null,
        };
      }
      return normalGet?.(path);
    });
    window.sessionStorage.setItem("nova.migration.lastJobId", "job-1");
    const screen = await render(<MigrationPage />);
    await expect
      .element(screen.getByText(/The worker could not complete the migration/))
      .toBeVisible();
    await expect
      .element(screen.getByText("unexpected_internal_detail"))
      .not.toBeInTheDocument();
  });

  it("does not select more databases than the batch limit", async () => {
    const normalPost = apiMocks.post.getMockImplementation();
    apiMocks.post.mockImplementation(
      async (path: string, body: Record<string, unknown>) => {
        if (path === "/migration/databases") {
          return {
            source: body.source,
            databases: Array.from({ length: 51 }, (_, index) => `db_${index}`),
            count: 51,
          };
        }
        return normalPost?.(path, body);
      },
    );
    const screen = await render(<MigrationPage />);
    await userEvent.fill(
      screen.getByRole("textbox", { name: "Connection name" }),
      "origin",
    );
    await userEvent.fill(
      screen.getByRole("textbox", { name: "IP or hostname" }),
      "10.0.0.12",
    );
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await expect
      .element(screen.getByText("Connection successful. You can save this source."))
      .toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Save source and list databases" }),
    );
    await expect
      .element(screen.getByRole("checkbox", { name: "db_50" }))
      .toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Select shown" }));
    await expect.element(screen.getByText("0 of 50 selected")).toBeVisible();
  });

  it("keeps source controls usable when worker status is unavailable", async () => {
    const normalGet = apiMocks.get.getMockImplementation();
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === "/migration/engine") throw new Error("Worker unavailable");
      return normalGet?.(path);
    });
    const screen = await render(<MigrationPage />);
    await expect
      .element(screen.getByRole("textbox", { name: "Connection name" }))
      .toBeEnabled();
    await expect
      .element(
        screen.getByText(/Sync utility status unavailable: Worker unavailable/),
      )
      .toBeVisible();
  });

  it("supports keyboard selection in the database list", async () => {
    const screen = await registerAndDiscover();
    await userEvent.click(screen.getByRole("checkbox", { name: "analytics" }));
    await expect.element(screen.getByText("1 of 50 selected")).toBeVisible();
    await userEvent.keyboard(" ");
    await expect.element(screen.getByText("0 of 50 selected")).toBeVisible();
  });

  it("keeps the database picker within a narrow viewport", async () => {
    await page.viewport(375, 800);
    const screen = await registerAndDiscover();
    expect(screen.container.scrollWidth).toBeLessThanOrEqual(
      screen.container.clientWidth,
    );
    await page.viewport(1280, 800);
    expect(screen.container.scrollWidth).toBeLessThanOrEqual(
      screen.container.clientWidth,
    );
  });
});
