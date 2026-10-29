import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { page, userEvent } from "vitest/browser";
import "@/styles/index.css";
import { ApiError } from "@/lib/api-client";
import { BiLaunch } from "./bi-launch";
import { safeBiTarget } from "./bi-target";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api-client")>()),
  api: mocks,
}));

describe("safeBiTarget", () => {
  it("keeps Superset paths", () => {
    expect(safeBiTarget("/bi/superset/dashboard/7/?native_filters=x")).toBe(
      "/bi/superset/dashboard/7/?native_filters=x",
    );
  });

  it.each([undefined, "", "/settings", "//evil.example/bi/", "https://evil.example/bi/"])(
    "sends %s to the BI home",
    (next) => {
      expect(safeBiTarget(next)).toBe("/bi/");
    },
  );
});

describe("BiLaunch", () => {
  beforeEach(() => vi.resetAllMocks());

  it("opens a BI session, then hands the browser to Superset", async () => {
    mocks.post.mockResolvedValue({ path: "/bi/", superset_role: "NovaAuthor" });
    const navigate = vi.fn();
    render(<BiLaunch next="/bi/sqllab/" navigate={navigate} />);

    await expect.poll(() => navigate.mock.calls).toEqual([["/bi/sqllab/"]]);
    expect(mocks.post).toHaveBeenCalledWith("/superset/session", undefined, expect.any(AbortSignal));
  });

  it("explains a refusal and retries on request", async () => {
    mocks.post
      .mockRejectedValueOnce(new ApiError(401, "Nova session has ended"))
      .mockResolvedValueOnce({ path: "/bi/", superset_role: "NovaViewer" });
    const navigate = vi.fn();
    render(<BiLaunch navigate={navigate} />);

    await expect.element(page.getByText("Nova session has ended")).toBeVisible();
    expect(navigate).not.toHaveBeenCalled();
    await userEvent.click(page.getByRole("button", { name: "Try again" }));
    await expect.poll(() => navigate.mock.calls).toEqual([["/bi/"]]);
  });
});
