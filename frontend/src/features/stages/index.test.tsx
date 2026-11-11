import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { StagesPage } from "./index";
import "@/styles/index.css";

const api = vi.hoisted(() => ({
  fetchStages: vi.fn(),
  createStage: vi.fn(),
  deleteStage: vi.fn(),
}));
vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  ...api,
}));

let client: QueryClient;

function setRole(activeRole: string) {
  useAuthStore.getState().auth.setUser({
    username: "switcher",
    roles: ["ACCOUNTADMIN", "loader"],
    activeRole,
    securityContextVersion: activeRole === "ACCOUNTADMIN" ? 1 : 2,
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  api.fetchStages.mockResolvedValue({ stages: [], count: 0 });
  api.createStage.mockImplementation(async (data) => ({
    ...data,
    id: "new-stage",
  }));
  setRole("loader");
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});

afterEach(() => {
  client.clear();
  useAuthStore.getState().auth.reset();
});

async function openCreate() {
  await render(
    <QueryClientProvider client={client}>
      <StagesPage />
    </QueryClientProvider>,
  );
  await page
    .getByRole("button", { name: "Create stage", exact: true })
    .first()
    .click();
}

it("does not offer an existing folder through an assigned but inactive admin role", async () => {
  await openCreate();
  const dialog = page.getByRole("dialog");
  await expect
    .element(dialog.getByLabelText("Base prefix", { exact: false }))
    .not.toBeInTheDocument();
  await dialog.getByLabelText("Name", { exact: true }).fill("orders");
  await dialog.getByLabelText("Database", { exact: true }).fill("analytics");
  await dialog.getByLabelText("Schema", { exact: true }).fill("bronze");
  await expect
    .element(dialog.getByRole("button", { name: "Create stage", exact: true }))
    .toBeDisabled();
  await dialog.getByLabelText("Storage connection").fill("production");
  await dialog
    .getByRole("button", { name: "Create stage", exact: true })
    .click();
  await expect
    .poll(() => api.createStage.mock.calls[0]?.[0])
    .toEqual({
      name: "orders",
      database_name: "analytics",
      schema_name: "bronze",
      storage_connection: "production",
      base_prefix: "",
    });
});

it("allows the active admin to choose an existing folder", async () => {
  setRole("ACCOUNTADMIN");
  await openCreate();
  await expect
    .element(
      page.getByRole("dialog").getByLabelText("Base prefix", { exact: false }),
    )
    .toBeVisible();
});

it("discards an admin draft and cached stage list when the role changes", async () => {
  setRole("ACCOUNTADMIN");
  api.fetchStages
    .mockResolvedValueOnce({
      stages: [
        {
          id: "private-stage",
          name: "private_files",
          database_name: "finance",
          schema_name: "restricted",
          storage_connection: "production",
          base_prefix: "private",
          created_at: null,
          created_by: "admin",
        },
      ],
      count: 1,
    })
    .mockResolvedValue({ stages: [], count: 0 });
  await openCreate();
  await page
    .getByRole("dialog")
    .getByLabelText("Base prefix", { exact: false })
    .fill("private");
  setRole("loader");
  await expect.element(page.getByRole("dialog")).not.toBeInTheDocument();
  await expect
    .element(page.getByText("private_files", { exact: false }))
    .not.toBeInTheDocument();
  await page
    .getByRole("button", { name: "Create stage", exact: true })
    .first()
    .click();
  await expect
    .element(page.getByRole("dialog").getByLabelText("Name", { exact: true }))
    .toHaveValue("");
  await expect
    .element(
      page.getByRole("dialog").getByLabelText("Base prefix", { exact: false }),
    )
    .not.toBeInTheDocument();
  await expect.poll(() => api.fetchStages.mock.calls.length).toBe(2);
});

it("keeps the draft editable after a failed creation", async () => {
  api.createStage.mockRejectedValueOnce(
    new Error("Storage connection is not configured"),
  );
  await openCreate();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabelText("Name", { exact: true }).fill("orders");
  await dialog.getByLabelText("Database", { exact: true }).fill("analytics");
  await dialog.getByLabelText("Schema", { exact: true }).fill("bronze");
  await dialog.getByLabelText("Storage connection").fill("unknown");
  await dialog
    .getByRole("button", { name: "Create stage", exact: true })
    .click();
  await expect.poll(() => api.createStage.mock.calls.length).toBe(1);
  await expect
    .element(dialog.getByLabelText("Storage connection"))
    .toHaveValue("unknown");
  await expect
    .element(dialog.getByRole("button", { name: "Create stage", exact: true }))
    .toBeEnabled();
});
