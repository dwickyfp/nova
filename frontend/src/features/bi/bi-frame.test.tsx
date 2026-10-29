import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { page } from "vitest/browser";
import "@/styles/index.css";
import { BiFrame } from "./bi-frame";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api-client")>()),
  api: mocks,
}));
vi.mock("@/context/theme-provider", () => ({ useTheme: () => ({ resolvedTheme: "dark" }) }));

describe("BiFrame", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.removeItem("superset-theme-mode");
  });

  it("opens the BI session, follows Nova's theme and frames the view", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    render(<BiFrame path="/bi/explore/?slice_id=21&standalone=1" title="BI chart Revenue" />);

    await expect
      .element(page.getByTitle("BI chart Revenue"))
      .toHaveAttribute("src", "/bi/explore/?slice_id=21&standalone=1");
    expect(localStorage.getItem("superset-theme-mode")).toBe("dark");
  });

  it("never frames a path outside BI", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaViewer" });
    render(<BiFrame path="//evil.example/" title="BI view" />);
    await expect.element(page.getByTitle("BI view")).toHaveAttribute("src", "/bi/");
  });
});
