import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/index.css";
import { ApiError } from "@/lib/api-client";
import { StudioTabs } from "../studio-tabs";
import { AddToDatavizButton } from "./add-to-dataviz";
import {
  attachDatavizFrame,
  parseFramePath,
  probeUnsavedChanges,
  syncDatavizFrame,
} from "./dashboard-bridge";

const dataviz = vi.hoisted(() => ({ check: vi.fn(), dashboards: vi.fn(), publish: vi.fn() }));
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  datavizApi: dataviz,
}));
const navigate = vi.hoisted(() => vi.fn());
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => navigate,
}));

const withQueries = (ui: React.ReactNode) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {ui}
  </QueryClientProvider>
);
const LOCATOR = { thread_id: "t1", message_id: "m1", content_id: "content-1" };
const SUPPORTED = {
  preview_ref: "pv1.a.b",
  status: "SUPPORTED",
  reason_code: "OK",
  message: "",
  can_add: true,
  title: "Units by brand",
  concepts: [{ qualified_name: "Sales.Units", label: "Units sold" }],
};

describe("the Dataviz frame bridge", () => {
  it("reads the dashboard and a well-formed filter key from a /bi path only", () => {
    expect(parseFramePath("/bi/superset/dashboard/14/?native_filters_key=Ab_9-x")).toMatchObject({
      dashboardId: 14,
      nativeFiltersKey: "Ab_9-x",
    });
    expect(parseFramePath("/bi/superset/dashboard/14/?native_filters_key=<script>")).toMatchObject({
      dashboardId: 14,
      nativeFiltersKey: null,
    });
    expect(parseFramePath("/bi/explore/?slice_id=3").dashboardId).toBeNull();
    expect(parseFramePath("https://evil.example/bi/superset/dashboard/1/").path).toBeNull();
  });

  it("reads unsaved edits from Superset's own beforeunload guard", () => {
    // A same-origin frame window: a real EventTarget at a Superset path.
    const view = Object.assign(new EventTarget(), {
      location: { pathname: "/bi/superset/dashboard/1/", search: "" },
      Event: window.Event,
    });
    const frame = { contentWindow: view } as unknown as HTMLIFrameElement;
    expect(probeUnsavedChanges(frame)).toBe("clean");
    // What Superset's useBeforeUnload registers while a dashboard has unsaved changes.
    view.addEventListener("beforeunload", (event) => event.preventDefault());
    expect(probeUnsavedChanges(frame)).toBe("dirty");
    const elsewhere = { contentWindow: { ...view, location: { pathname: "/sign-in", search: "" } } };
    expect(probeUnsavedChanges(elsewhere as unknown as HTMLIFrameElement)).toBe("unknown");
    expect(probeUnsavedChanges(null)).toBe("unknown");
  });
});

describe("Studio's workspace tabs", () => {
  it("is one tab list that arrow keys move through", async () => {
    const onSelect = vi.fn();
    const screen = await render(<StudioTabs tab="agent" onSelect={onSelect} />);
    const agent = screen.getByRole("tab", { name: "Agent" });
    await expect.element(agent).toHaveAttribute("aria-selected", "true");
    await expect.element(screen.getByRole("tab", { name: "Dataviz" })).toHaveAttribute("tabindex", "-1");
    await userEvent.click(agent);
    await userEvent.keyboard("{ArrowRight}");
    expect(onSelect).toHaveBeenLastCalledWith("dataviz");
  });

  it.each([
    [700, false],
    [1200, true],
  ])("takes its own row above a page header in a %ipx Studio pane (floats: %s)", async (width, floats) => {
    const screen = await render(
      <main className="@container/studio relative flex h-96 flex-col" style={{ width }}>
        <StudioTabs tab="agent" onSelect={vi.fn()} reserveRow />
        <header className="flex h-14 shrink-0 items-center justify-center">
          <button type="button">Edit chart</button>
        </header>
      </main>,
    );
    const tabs = screen.getByRole("tablist").element().getBoundingClientRect();
    const button = screen.getByRole("button", { name: "Edit chart" }).element().getBoundingClientRect();
    if (floats) expect(button.top).toBeLessThan(tabs.bottom);
    else expect(tabs.bottom).toBeLessThanOrEqual(button.top);
  });
});

describe("Add to Dataviz", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    dataviz.dashboards.mockResolvedValue({
      dashboards: [{ id: 7, title: "Sales", published: true, own: true, bi_view_path: "/bi/superset/dashboard/7/" }],
      page: 0,
      has_more: false,
    });
  });
  afterEach(() => document.body.querySelectorAll("[role=dialog]").forEach((node) => node.remove()));

  it.each([
    ["UNSUPPORTED", false],
    ["CONVERTIBLE", false],
    ["UNKNOWN", false],
    ["SUPPORTED", false],
  ])("shows nothing at all for %s when it cannot be added", async (status, canAdd) => {
    dataviz.check.mockResolvedValue({ results: [{ ...SUPPORTED, status, can_add: canAdd }] });
    const screen = await render(withQueries(<div><AddToDatavizButton locator={LOCATOR} /></div>));
    await vi.waitFor(() => expect(dataviz.check).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.container.textContent).toBe("");
  });

  it("shows nothing when the check itself fails", async () => {
    dataviz.check.mockRejectedValue(new Error("down"));
    const screen = await render(withQueries(<div><AddToDatavizButton locator={LOCATOR} /></div>));
    await vi.waitFor(() => expect(dataviz.check).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.container.querySelector("button")).toBeNull();
  });

  it("adds to a chosen dashboard and retries with the same request id", async () => {
    dataviz.check.mockResolvedValue({ results: [SUPPORTED] });
    dataviz.publish
      .mockRejectedValueOnce(new ApiError(503, "Dataviz is unavailable right now."))
      .mockResolvedValueOnce({
        operation_id: "pub_1", status: "SUCCEEDED", dashboard_id: 7, chart_id: 9,
        artifact_id: "a", bi_view_path: "/bi/superset/dashboard/7/", reused_chart: false, concepts: [],
      });
    const screen = await render(withQueries(<AddToDatavizButton locator={LOCATOR} />));
    await screen.getByRole("button", { name: "Add to Dataviz" }).click();
    await expect.element(screen.getByText("Business meaning: Units sold")).toBeVisible();
    await screen.getByRole("option", { name: /Sales/ }).click();
    await screen.getByRole("button", { name: "Add chart" }).click();
    await expect.element(screen.getByRole("alert")).toHaveTextContent("Dataviz is unavailable right now.");
    await screen.getByRole("button", { name: "Add chart" }).click();
    await vi.waitFor(() => expect(dataviz.publish).toHaveBeenCalledTimes(2));
    const [first, second] = dataviz.publish.mock.calls.map(([body]) => body);
    expect(first.idempotency_key).toBe(second.idempotency_key);
    expect(first).toMatchObject({ preview_ref: "pv1.a.b", target: { mode: "existing", dashboard_id: 7 } });
  });

  it("creates a new dashboard by name, without asking for SQL or a dataset", async () => {
    dataviz.check.mockResolvedValue({ results: [SUPPORTED] });
    dataviz.publish.mockResolvedValue({
      operation_id: "pub_2", status: "SUCCEEDED", dashboard_id: 11, chart_id: 9,
      artifact_id: "a", bi_view_path: "/bi/superset/dashboard/11/", reused_chart: false, concepts: [],
    });
    const screen = await render(withQueries(<AddToDatavizButton locator={LOCATOR} />));
    await screen.getByRole("button", { name: "Add to Dataviz" }).click();
    await screen.getByRole("radio", { name: "New dashboard" }).click();
    await screen.getByRole("button", { name: "Create and add" }).click();
    await expect.element(screen.getByRole("alert")).toHaveTextContent("Name the new dashboard.");
    await screen.getByLabelText("Dashboard name").fill("Overview");
    await screen.getByRole("button", { name: "Create and add" }).click();
    await vi.waitFor(() =>
      expect(dataviz.publish.mock.calls[0][0].target).toEqual({ mode: "new", title: "Overview" }),
    );
    expect(screen.container.textContent).not.toMatch(/SQL|dataset/i);
  });
});

describe("the Dataviz assistant composer", () => {
  it("has no agent picker; the Agent tab keeps it", async () => {
    const { Composer } = await import("../studio-composer");
    const { DATAVIZ_ASSISTANT } = await import("./assistant-agent");
    const props = {
      ref: { current: null }, value: "", onChange: () => {}, onKeyDown: () => {}, onSend: () => {},
      onStop: () => {}, readingFiles: false, attachments: [], onAddFiles: () => {},
      onRemoveAttachment: () => {}, streaming: false, disabled: false,
      agent: DATAVIZ_ASSISTANT, agents: [DATAVIZ_ASSISTANT], onSelectAgent: () => {},
    };
    const panel = await render(<Composer {...props} agentPicker={false} />);
    expect(panel.container.textContent).not.toContain("Nova Dataviz assistant");
    await panel.unmount();
    const agentTab = await render(<Composer {...props} />);
    await expect.element(agentTab.getByText("Nova Dataviz assistant")).toBeVisible();
  });
});

describe("the dashboard the assistant reads", () => {
  function framed(path: string, title: string) {
    const [pathname, search = ""] = path.split("?");
    return {
      contentWindow: { location: { pathname, search: search ? `?${search}` : "" } },
      contentDocument: { title },
    } as unknown as HTMLIFrameElement;
  }

  afterEach(() => attachDatavizFrame(null));

  it("takes the open dashboard's name from Superset's page title", () => {
    attachDatavizFrame(framed("/bi/superset/dashboard/16/", "Dataviz 5W1H eval"));
    expect(syncDatavizFrame()).toMatchObject({ dashboardId: 16, title: "Dataviz 5W1H eval" });
    attachDatavizFrame(framed("/bi/superset/dashboard/16/", "Sales | Superset"));
    expect(syncDatavizFrame().title).toBe("Sales");
  });

  it("names nothing outside a dashboard or before Superset titles it", () => {
    attachDatavizFrame(framed("/bi/explore/", "Revenue chart"));
    expect(syncDatavizFrame().title).toBeNull();
    attachDatavizFrame(framed("/bi/superset/dashboard/16/", "Superset"));
    expect(syncDatavizFrame().title).toBeNull();
  });
});
