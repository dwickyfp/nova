import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { page } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/index.css";
import { AccessControlPage } from "./index";

vi.mock("@/components/layout/header", () => ({
  Header: ({ children }: { children?: React.ReactNode }) => <header>{children}</header>,
}));
vi.mock("@/components/search", () => ({ Search: () => null }));

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api-client")>()),
  api: mocks,
}));

const DATA: Record<string, unknown> = {
  "/access-control/health": { healthy: true, pending_propagation: 3, drift_count: 0 },
  "/access-control/roles": {
    roles: [
      { id: 1, name: "SALES_JAKARTA", description: "Sales team in Jakarta", users: [{ name: "ana" }] },
      { id: 2, name: "FINANCE", users: [] },
    ],
  },
  "/access-control/policies": {
    policies: [
      { id: 1, name: "nova_sales_orders", policyType: 0,
        resources: { database: { values: ["RETAIL"] }, table: { values: ["orders"] } },
        policyItems: [{ roles: ["SALES_JAKARTA"], accesses: [{ type: "select" }] }] },
      { id: 2, name: "nova_mask_phone", policyType: 1,
        resources: { column: { values: ["phone"] } },
        dataMaskPolicyItems: [{ roles: ["SALES_JAKARTA"] }] },
    ],
  },
  "/users": { users: [{ username: "ana" }] },
  "/users/databases": { databases: ["RETAIL"] },
};

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AccessControlPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mocks.get.mockImplementation(async (path: string) => DATA[path] ?? {});
});

afterEach(async () => {
  await cleanup();
  vi.clearAllMocks();
});

describe("Access Control", () => {
  it("keeps the Ranger status with the page, not with one tab", async () => {
    const screen = await mount();
    const status = screen.getByLabelText("Authorization status");
    await expect.element(status).toHaveTextContent("Ranger healthy");
    await expect.element(status).toHaveTextContent("3 changes syncing");
    // The status sits before the tabs and stays put when a tab changes.
    const tabs = screen.getByRole("tablist").element();
    expect(
      status.element().compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    await screen.getByRole("tab", { name: "Check access" }).click();
    await expect.element(status).toBeVisible();
  });

  it("explains each tab in plain words", async () => {
    const screen = await mount();
    await expect.element(screen.getByText(/A role is a group of people/)).toBeVisible();
    await expect.element(screen.getByText("Sales team in Jakarta")).toBeVisible();

    await screen.getByRole("tab", { name: "Data access" }).click();
    await expect.element(screen.getByRole("heading", { name: "Let a role use a table" })).toBeVisible();
    await expect.element(screen.getByRole("heading", { name: "Show a person only some rows" })).toBeVisible();
    await expect.element(screen.getByRole("heading", { name: "Hide a sensitive column" })).toBeVisible();
    await expect.element(screen.getByRole("combobox", { name: "Allowed to" })).toHaveTextContent("Read");

    await screen.getByRole("tab", { name: "Policies" }).click();
    await expect.element(screen.getByText(/Nova writes them for you/)).toBeVisible();
    await expect.element(screen.getByText("Table access", { exact: true })).toBeVisible();
    await expect.element(screen.getByText("Hidden column", { exact: true })).toBeVisible();
  });

  it("reads a checked result in words", async () => {
    mocks.post.mockResolvedValue({
      principal: "ana",
      active_role: "SALES_JAKARTA",
      resource: "RETAIL.orders",
      authorization_provider: "Ranger",
      policy_health: "healthy",
      policies: [],
      object_access: ["SELECT"],
      row_restrictions: [],
      column_restrictions: { phone: "MASK" },
    });
    const screen = await mount();
    await screen.getByRole("tab", { name: "Check access" }).click();
    const pick = async (label: string, option: string) => {
      await screen.getByRole("button", { name: label, exact: true }).click();
      await screen.getByRole("button", { name: option, exact: true }).click();
    };
    await pick("Person", "ana");
    await pick("Using role", "SALES_JAKARTA");
    mocks.get.mockImplementation(async (path: string) =>
      path.endsWith("/tables") ? { tables: ["orders"] } : DATA[path] ?? {});
    await pick("Database", "RETAIL");
    await pick("Table", "orders");
    await screen.getByRole("button", { name: "Check access" }).click();
    await expect.element(screen.getByText("Read", { exact: true })).toBeVisible();
    await expect.element(screen.getByText("All rows")).toBeVisible();
    await expect.element(screen.getByText("phone (show as xxxx)")).toBeVisible();
    for (const width of [1280, 390]) {
      await page.viewport(width, 900);
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
    }
    await page.viewport(1280, 720);
  });
});
