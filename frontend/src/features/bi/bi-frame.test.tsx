import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { page } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/index.css";
import { BiFrame } from "./bi-frame";
import { stillDrawing } from "./superset-loading";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api-client")>()),
  api: mocks,
}));
const theme = vi.hoisted(() => ({
  value: { theme: "dark" as "dark" | "light" | "system", resolvedTheme: "dark" as "dark" | "light" },
}));
vi.mock("@/context/theme-provider", () => ({ useTheme: () => theme.value }));

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe("BiFrame", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    theme.value = { theme: "dark", resolvedTheme: "dark" };
    localStorage.removeItem("superset-theme-mode");
  });

  it("opens the BI session, follows Nova's theme and frames the view", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    render(
      <QueryClientProvider client={client()}>
        <BiFrame path="/bi/explore/?slice_id=21&standalone=1" title="Chart Revenue" />
      </QueryClientProvider>,
    );

    await expect
      .element(page.getByTitle("Chart Revenue"))
      .toHaveAttribute("src", "/bi/explore/?slice_id=21&standalone=1");
    expect(localStorage.getItem("superset-theme-mode")).toBe("dark");
  });

  it("covers the frame with Nova's loading state until the page loads", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    render(
      <QueryClientProvider client={client()}>
        <BiFrame path="/bi/" title="BI view" />
      </QueryClientProvider>,
    );
    const frame = page.getByTitle("BI view");
    await expect.element(frame).toHaveAttribute("src", "/bi/");
    frame.element().dispatchEvent(new Event("load"));
    await expect.element(page.getByRole("status")).not.toBeInTheDocument();
    await expect.element(frame).toHaveClass("opacity-100");
  });

  it("waits for Superset's own spinner before showing the frame", () => {
    const framed = (body: string) => {
      const doc = document.implementation.createHTMLDocument("bi");
      doc.body.innerHTML = body;
      return { contentDocument: doc } as unknown as HTMLIFrameElement;
    };
    expect(stillDrawing(framed('<div role="status" aria-label="Loading"></div>'))).toBe(true);
    expect(stillDrawing(framed("<canvas></canvas>"))).toBe(false);
    expect(stillDrawing(null)).toBe(false);
    const blocked = {} as HTMLIFrameElement;
    Object.defineProperty(blocked, "contentDocument", {
      get: () => {
        throw new DOMException("cross-origin", "SecurityError");
      },
    });
    expect(stillDrawing(blocked)).toBe(false);
  });

  it("swaps to a new version under the shown view, never through an empty frame", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    const queries = client();
    const screen = await render(
      <QueryClientProvider client={queries}>
        <BiFrame path="/bi/explore/?slice_id=1&standalone=1" version="v1" title="Chart" />
      </QueryClientProvider>,
    );
    const first = page.getByTitle("Chart");
    await expect.element(first).toHaveAttribute("src", "/bi/explore/?slice_id=1&standalone=1");
    first.element().dispatchEvent(new Event("load"));
    await expect.element(first).toHaveClass("opacity-100");

    await screen.rerender(
      <QueryClientProvider client={queries}>
        <BiFrame path="/bi/explore/?form_data_key=K1&standalone=1" version="v1" title="Chart" />
      </QueryClientProvider>,
    );
    // The new view loads under the old one, which stays on screen, dimmed,
    // and leaves the accessibility tree; Nova's full loading state does not
    // come back.
    const frames = () => [...document.querySelectorAll("iframe")];
    expect(frames()).toHaveLength(2);
    const [old, next] = frames();
    expect(old.getAttribute("aria-hidden")).toBe("true");
    expect(old.className).toContain("opacity-50");
    expect(next.getAttribute("title")).toBe("Chart");
    await expect.element(page.getByRole("status")).toHaveTextContent("Updating");

    next.dispatchEvent(new Event("load"));
    await expect.poll(() => frames().length).toBe(1);
    expect(frames()[0].getAttribute("src")).toBe("/bi/explore/?form_data_key=K1&standalone=1");
    expect(frames()[0].className).toContain("opacity-100");
    await expect.element(page.getByRole("status")).not.toBeInTheDocument();
  });

  it("opens one BI session for every frame on the page", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaViewer" });
    render(
      <QueryClientProvider client={client()}>
        <BiFrame path="/bi/explore/?slice_id=1&standalone=1" title="Chart A" />
        <BiFrame path="/bi/explore/?slice_id=2&standalone=1" title="Chart B" />
      </QueryClientProvider>,
    );
    await expect.element(page.getByTitle("Chart A")).toBeInTheDocument();
    await expect.element(page.getByTitle("Chart B")).toBeInTheDocument();
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });

  it("reloads a view for a new theme but never an editor", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    const queries = client();
    const screen = await render(
      <QueryClientProvider client={queries}>
        <BiFrame path="/bi/explore/?slice_id=1&standalone=1" title="Chart" />
        <BiFrame path="/bi/explore/?slice_id=1&uiConfig=4" title="Editor" holdsEdits />
      </QueryClientProvider>,
    );
    await expect.element(page.getByTitle("Chart")).toBeInTheDocument();
    await expect.element(page.getByTitle("Editor")).toBeInTheDocument();
    const view = page.getByTitle("Chart").element();
    const editor = page.getByTitle("Editor").element();

    theme.value = { theme: "light", resolvedTheme: "light" };
    await screen.rerender(
      <QueryClientProvider client={queries}>
        <BiFrame path="/bi/explore/?slice_id=1&standalone=1" title="Chart" />
        <BiFrame path="/bi/explore/?slice_id=1&uiConfig=4" title="Editor" holdsEdits />
      </QueryClientProvider>,
    );

    expect(page.getByTitle("Chart").element()).not.toBe(view);
    expect(page.getByTitle("Editor").element()).toBe(editor);
    // The editor takes the new theme the next time Superset loads a page.
    expect(localStorage.getItem("superset-theme-mode")).toBe("default");
  });

  it("lets Superset follow the OS when Nova does", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    theme.value = { theme: "system", resolvedTheme: "dark" };
    render(
      <QueryClientProvider client={client()}>
        <BiFrame path="/bi/" title="BI view" />
      </QueryClientProvider>,
    );
    await expect.element(page.getByTitle("BI view")).toBeInTheDocument();
    expect(localStorage.getItem("superset-theme-mode")).toBe("system");
  });

  it("never frames a path outside BI", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaViewer" });
    render(
      <QueryClientProvider client={client()}>
        <BiFrame path="//evil.example/" title="BI view" />
      </QueryClientProvider>,
    );
    await expect.element(page.getByTitle("BI view")).toHaveAttribute("src", "/bi/");
  });
});
