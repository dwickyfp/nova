import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SidebarProvider } from "@/components/ui/sidebar";
import { AssistantProvider } from "@/features/assistant/assistant-provider";
import { useAuthStore } from "@/stores/auth-store";
import type { WorkspaceEntry, WorkspaceTreeResponse } from "./types";
import { WorkspacesPage } from "./index";
import "@/styles/index.css";

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  getRouteApi: () => ({ useSearch: () => ({}) }),
  useNavigate: () => vi.fn(),
}));

// Monaco loads from a CDN; a textarea keeps the editor's contract.
vi.mock("@monaco-editor/react", () => ({
  default: ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (value?: string) => void;
  }) => (
    <textarea
      aria-label="Editor content"
      value={value ?? ""}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}));

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * A workspace whose files were all deleted while their tabs stayed in the saved
 * state: `open_tabs` and `active_tab` still name a file that no longer exists.
 * It keeps the files the page creates.
 */
function fakeBackend() {
  const entries: WorkspaceEntry[] = [];
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.includes("/workspaces/tree")) {
      const tree: WorkspaceTreeResponse = {
        root_name: "workspace",
        entries: [...entries],
        open_tabs: ["deleted-file"],
        active_tab: "deleted-file",
        sidebar_collapsed: false,
        assistant_collapsed: true,
        defaults: { database: "analytics", schema: "public", role: "marketing" },
      };
      return json(tree);
    }
    if (url.endsWith("/workspaces/files") && method === "POST") {
      const body = JSON.parse(String(init?.body)) as { name: string };
      const entry: WorkspaceEntry = {
        id: `file-${entries.length + 1}`,
        name: body.name,
        parent_path: "",
        path: body.name,
        entry_type: "file",
        size_bytes: 0,
      };
      entries.push(entry);
      return json({ entry, content: "" });
    }
    if (url.includes("/query/context")) {
      return json({
        roles: ["marketing"],
        databases: ["analytics"],
        schemas: ["public"],
        defaults: { database: "analytics", schema: "public", role: "marketing" },
      });
    }
    if (url.includes("/schemas")) return json({ schemas: [{ name: "public" }] });
    if (url.includes("/assistant/threads")) return json({ threads: [], count: 0 });
    return json({});
  });
}

describe("Workspaces first file", () => {
  beforeEach(() => {
    useAuthStore.getState().auth.setUser({
      username: "alice",
      roles: ["marketing"],
      activeRole: "marketing",
    } as never);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useAuthStore.getState().auth.setUser(null);
  });

  it("opens an editor for the first file when saved tabs name deleted files", async () => {
    fakeBackend();
    const errors = vi.spyOn(console, "error");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const screen = await render(
      <QueryClientProvider client={client}>
        <SidebarProvider>
          <AssistantProvider>
            <WorkspacesPage />
          </AssistantProvider>
        </SidebarProvider>
      </QueryClientProvider>,
    );

    await userEvent.click(page.getByRole("button", { name: "New File", exact: true }));

    await expect.element(page.getByRole("textbox", { name: "Editor content" })).toBeVisible();
    // Give a runaway effect time to show itself before checking the log.
    await new Promise((resolve) => setTimeout(resolve, 500));
    const loops = errors.mock.calls.filter((call) =>
      call.some((part) => String(part).includes("Maximum update depth")),
    );
    expect(loops).toHaveLength(0);
    expect(page.getByText("Something went wrong").elements()).toHaveLength(0);

    // Unmount while the fake backend still answers: the page's debounced
    // state save would otherwise fire after afterEach restores the real fetch.
    await screen.unmount();
    client.clear();
  });
});
