import { page } from "vitest/browser";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { WorkspaceTreeResponse } from "@/features/workspaces/types";
import { AssistantProvider, useAssistant } from "./assistant-provider";
import { AssistantDock } from "./assistant-dock";

const GRANT_PROMPT = "Grant SELECT on NOVA_SALES.fact_sales to ACCOUNTADMIN";

function GrantLaunch() {
  const { newChatAndSend } = useAssistant();
  return (
    <button type="button" onClick={() => newChatAndSend(GRANT_PROMPT)}>
      Submit grant request
    </button>
  );
}

function makeTree(
  overrides: Partial<WorkspaceTreeResponse> = {},
): WorkspaceTreeResponse {
  return {
    root_name: "workspace",
    entries: [],
    open_tabs: [],
    active_tab: null,
    sidebar_collapsed: false,
    assistant_collapsed: false,
    defaults: { database: "analytics", schema: "public", role: "ACCOUNTADMIN" },
    ...overrides,
  };
}

function mockTree(tree: WorkspaceTreeResponse) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      if (String(input).includes("/workspaces/tree")) {
        return new Response(JSON.stringify(tree), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      // The panel's empty state reads the recent-threads list; answer it with an
      // empty list so the query resolves instead of logging a shape error.
      if (
        String(input).endsWith("/assistant/threads") &&
        init?.method === "POST"
      ) {
        return new Response(JSON.stringify({ thread_id: "grant-thread" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (String(input).endsWith("/assistant/threads/grant-thread/messages")) {
        return new Response("", { status: 200 });
      }
      if (String(input).includes("/assistant/threads")) {
        return new Response(JSON.stringify({ threads: [], count: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(null, { status: 204 });
    });
}

/**
 * Mirrors the layout's structure: the provider wraps the route slot, so a route
 * change swaps the slot contents without remounting the assistant. This is the
 * production shape with no WorkspacesPage in the tree.
 */
function LayoutHarness({ route }: { route: string }) {
  return (
    <div className="flex h-svh">
      <div data-testid="route">{route}</div>
      <GrantLaunch />
      <AssistantDock />
    </div>
  );
}

function renderLayout(tree: WorkspaceTreeResponse, route = "dashboard") {
  mockTree(tree);
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <AssistantProvider>
        <LayoutHarness route={route} />
      </AssistantProvider>
    </QueryClientProvider>,
  );
}

describe("AssistantDock", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens a fresh chat and submits the access grant request", async () => {
    await page.viewport(1440, 900);
    try {
      const { getByRole, getByPlaceholder, getByText } = await renderLayout(
        makeTree({ assistant_collapsed: true }),
        "agents",
      );

      await getByRole("button", { name: "Submit grant request" }).click();

      await expect
        .element(getByRole("button", { name: "Close assistant" }))
        .toBeInTheDocument();
      await expect
        .element(getByPlaceholder("Ask a question or describe a query"))
        .toHaveValue("");
      await expect.element(getByText(GRANT_PROMPT)).toBeInTheDocument();
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("starts closed even when the saved panel preference is open", async () => {
    await page.viewport(1440, 900);
    try {
      const { getByRole, container } = await renderLayout(
        makeTree({ assistant_collapsed: false }),
      );

      await expect
        .element(getByRole("button", { name: "Ask Nove" }))
        .toBeInTheDocument();
      await expect
        .poll(() =>
          container.querySelector("#assistant-panel")?.hasAttribute("inert"),
        )
        .toBe(true);
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("renders a collapsed panel when the tree says it is collapsed", async () => {
    await page.viewport(1440, 900);
    try {
      const { getByRole, container } = await renderLayout(
        makeTree({ assistant_collapsed: true }),
      );

      await expect
        .element(getByRole("button", { name: "Ask Nove" }))
        .toBeInTheDocument();
      expect(
        container.querySelector("#assistant-panel")?.hasAttribute("inert"),
      ).toBe(true);
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("opens from the FAB and closes from the header control", async () => {
    await page.viewport(1440, 900);
    try {
      const { getByRole, container } = await renderLayout(
        makeTree({ assistant_collapsed: true }),
      );

      await getByRole("button", { name: "Ask Nove" }).click();
      await expect
        .element(getByRole("button", { name: "Close assistant" }))
        .toBeInTheDocument();
      expect(
        container.querySelector("#assistant-panel")?.hasAttribute("inert"),
      ).toBe(false);

      await getByRole("button", { name: "Close assistant" }).click();
      await expect
        .element(getByRole("button", { name: "Ask Nove" }))
        .toBeInTheDocument();
      expect(
        container.querySelector("#assistant-panel")?.hasAttribute("inert"),
      ).toBe(true);
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("keeps the open state across a route change", async () => {
    await page.viewport(1440, 900);
    try {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      mockTree(makeTree({ assistant_collapsed: true }));
      const { getByRole, rerender } = await render(
        <QueryClientProvider client={client}>
          <AssistantProvider>
            <LayoutHarness route="dashboard" />
          </AssistantProvider>
        </QueryClientProvider>,
      );

      await getByRole("button", { name: "Ask Nove" }).click();
      await expect
        .element(getByRole("button", { name: "Close assistant" }))
        .toBeInTheDocument();

      // A route change swaps the slot but keeps the provider, so the assistant
      // (and its open state) is not remounted.
      await rerender(
        <QueryClientProvider client={client}>
          <AssistantProvider>
            <LayoutHarness route="database-explorer" />
          </AssistantProvider>
        </QueryClientProvider>,
      );

      await expect
        .element(getByRole("button", { name: "Close assistant" }))
        .toBeInTheDocument();
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("guards the panel transition against reduced motion", async () => {
    await page.viewport(1440, 900);
    try {
      const { container } = await renderLayout(
        makeTree({ assistant_collapsed: false }),
      );

      const aside = container.querySelector("#assistant-panel");
      expect(aside?.parentElement?.className).toContain(
        "motion-reduce:transition-none",
      );
      expect(aside?.parentElement?.className).toContain("transition-[width]");
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("hides the floating FAB once the panel is open", async () => {
    await page.viewport(1440, 900);
    try {
      // The browser test runner does not load the Tailwind stylesheet, so the
      // measured geometry is the UA default. Assert the position contract here;
      // the live click-through measures the real boxes.
      const { getByRole, container } = await renderLayout(
        makeTree({ assistant_collapsed: true }),
      );

      // The toggle anchors to the right edge through a wrapper whose `bottom`
      // is inline (the draggable offset), so the position contract is the
      // wrapper's style rather than a `bottom-4` class.
      const closed = getByRole("button", { name: "Ask Nove" }).element();
      const anchor = closed.closest("div[style]") as HTMLElement | null;
      // The default `bottom-4` (1rem) plus a zero offset; the browser may order
      // the two addends either way.
      expect(anchor?.style.bottom).toContain("1rem");
      expect(anchor?.style.bottom).toContain("0px");

      await getByRole("button", { name: "Ask Nove" }).click();
      // Closing lives in the header; no FAB floats over it.
      await expect
        .poll(
          () =>
            container.querySelectorAll('button[aria-label="Ask Nove"]').length,
        )
        .toBe(0);
      await expect
        .element(getByRole("button", { name: "Close assistant" }))
        .toBeInTheDocument();
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("ladders the panel inline at 768px and as a Sheet just below it", async () => {
    try {
      await page.viewport(768, 900);
      const wide = await renderLayout(
        makeTree({ assistant_collapsed: false }),
        "database-explorer",
      );
      await wide.getByRole("button", { name: "Ask Nove" }).click();
      await expect
        .element(wide.getByRole("complementary", { name: "Nove" }))
        .toBeInTheDocument();
      await expect
        .element(wide.getByRole("button", { name: "Close assistant" }))
        .toBeInTheDocument();
      wide.unmount();

      await page.viewport(767, 900);
      const narrow = await renderLayout(
        makeTree({ assistant_collapsed: false }),
        "database-explorer",
      );
      await expect.element(narrow.getByRole("dialog")).not.toBeInTheDocument();
      await narrow.getByRole("button", { name: "Ask Nove" }).click();
      // Below md the panel is a Sheet overlay with its own close control; the
      // FAB is the trigger that opened it and is inert behind the overlay.
      const dialog = narrow.getByRole("dialog");
      await expect.element(dialog).toBeInTheDocument();
      await expect
        .element(narrow.getByRole("button", { name: "Close assistant" }))
        .toBeInTheDocument();
      narrow.unmount();
    } finally {
      await page.viewport(375, 800);
    }
  });
});
