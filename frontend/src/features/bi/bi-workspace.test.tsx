import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { page, userEvent } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/index.css";
import { ApiError } from "@/lib/api-client";
import { BiWorkspace } from "./bi-workspace";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api-client")>()),
  api: mocks,
}));
vi.mock("@/components/layout/header", () => ({
  Header: ({ children }: { children?: React.ReactNode }) => <header>{children}</header>,
}));

const withQueries = (ui: React.ReactNode) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>
);

describe("BiWorkspace", () => {
  beforeEach(() => vi.resetAllMocks());

  it("frames Superset inside Nova once the BI session is open", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    render(withQueries(<BiWorkspace path="/bi/sqllab/" onPathChange={vi.fn()} />));

    await expect.element(page.getByRole("heading", { name: "BI" })).toBeVisible();
    const frame = page.getByTitle("BI workspace");
    await expect.element(frame).toHaveAttribute("src", "/bi/sqllab/");
    expect(mocks.post).toHaveBeenCalledWith("/superset/session", undefined, expect.any(AbortSignal));
    await expect
      .element(page.getByRole("link", { name: "Open in new tab" }))
      .toHaveAttribute("href", "/bi-launch?next=%2Fbi%2Fsqllab%2F");
  });

  it("never frames a path outside Superset", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaViewer" });
    render(withQueries(<BiWorkspace path="https://evil.example/" onPathChange={vi.fn()} />));

    await expect.element(page.getByTitle("BI workspace")).toHaveAttribute("src", "/bi/");
  });

  it("explains a refusal and retries on request", async () => {
    mocks.post
      .mockRejectedValueOnce(new ApiError(401, "Nova session has ended"))
      .mockResolvedValueOnce({ path: "/bi/", superset_role: "NovaViewer" });
    render(withQueries(<BiWorkspace onPathChange={vi.fn()} />));

    await expect.element(page.getByText("Nova session has ended")).toBeVisible();
    await userEvent.click(page.getByRole("button", { name: "Try again" }));
    await expect.element(page.getByTitle("BI workspace")).toBeVisible();
  });
});
