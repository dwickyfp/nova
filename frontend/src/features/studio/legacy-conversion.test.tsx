import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { LegacyBanner, LegacyNotice } from "./legacy-conversion";

const mocks = vi.hoisted(() => ({ convertArtifacts: vi.fn() }));

vi.mock("@/features/agents/api", () => ({ studioApi: mocks }));

function outcome(artifact_id: string, status: "converted" | "kept" | "failed", blockers: string[] = []) {
  return {
    artifact_id,
    title: artifact_id === "c1" ? "Revenue" : "Margin",
    artifact_type: "chart" as const,
    status,
    blockers,
    notes: status === "converted" ? ["sort order"] : [],
    error: status === "failed" ? "The dataset could not be created" : null,
    bi_view_path: status === "converted" ? "/bi/superset/explore/?slice_id=3" : null,
  };
}

async function mount(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>);
}

describe("legacy conversion", () => {
  beforeEach(() => {
    mocks.convertArtifacts.mockReset().mockResolvedValue({ outcomes: [outcome("c1", "converted")] });
  });

  it("converts a view without blockers in one step", async () => {
    const screen = await mount(<LegacyBanner artifactId="c1" blockers={[]} notes={["sort order"]} />);
    await expect.element(screen.getByText("Saved before BI, read-only")).toBeVisible();
    await expect.element(screen.getByText("sort order")).toBeVisible();
    await screen.getByRole("button", { name: "Convert to BI" }).click();
    expect(mocks.convertArtifacts).toHaveBeenCalledWith({ artifact_ids: ["c1"], force: false });
  });

  it("asks before converting a view BI computes differently", async () => {
    const screen = await mount(
      <LegacyBanner artifactId="c2" blockers={["table calculations (running_total)"]} notes={[]} />,
    );
    await screen.getByRole("button", { name: "Convert anyway" }).click();
    const dialog = screen.getByRole("dialog");
    await expect.element(dialog.getByText("table calculations (running_total)")).toBeVisible();
    expect(mocks.convertArtifacts).not.toHaveBeenCalled();
    await dialog.getByRole("button", { name: "Convert anyway" }).click();
    expect(mocks.convertArtifacts).toHaveBeenCalledWith({ artifact_ids: ["c2"], force: true });
  });

  it("reports every view after converting all, and converts kept views on request", async () => {
    mocks.convertArtifacts.mockResolvedValueOnce({
      outcomes: [outcome("c1", "converted"), outcome("c2", "kept", ["calculated fields (Margin)"]), outcome("c3", "failed")],
    });
    const screen = await mount(<LegacyNotice count={3} />);
    await expect.element(screen.getByText(/3 views were saved before BI/)).toBeVisible();
    await screen.getByRole("button", { name: "Convert all" }).click();
    expect(mocks.convertArtifacts).toHaveBeenCalledWith({});

    const report = screen.getByRole("dialog");
    await expect.element(report.getByText("1 in BI", { exact: false })).toBeVisible();
    await expect.element(report.getByText("Kept read-only", { exact: true })).toBeVisible();
    await expect.element(report.getByText("calculated fields (Margin)")).toBeVisible();
    await expect.element(report.getByText("The dataset could not be created")).toBeVisible();
    await report.getByRole("button", { name: "Convert the kept view anyway" }).click();
    expect(mocks.convertArtifacts).toHaveBeenLastCalledWith({ artifact_ids: ["c2"], force: true });
  });
});
