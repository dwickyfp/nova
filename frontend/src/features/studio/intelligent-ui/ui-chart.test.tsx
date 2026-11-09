import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { UiChart } from "./ui-chart";
import "@/styles/index.css";

const dataviz = vi.hoisted(() => ({ check: vi.fn() }));
vi.mock("../dataviz/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../dataviz/api")>()),
  datavizApi: dataviz,
}));

const spec = JSON.stringify({
  title: "Revenue by salesperson",
  mark: "bar",
  encoding: {
    y: { field: "salesperson", type: "nominal", sort: "-x" },
    x: { field: "revenue", type: "quantitative" },
  },
  data: { values: [{ salesperson: "Budi", revenue: 1200 }, { salesperson: "Rizky", revenue: 3200 }] },
});

describe("a stored chart in Studio", () => {
  it("renders and asks about the stored chart block, not its data table", async () => {
    dataviz.check.mockResolvedValue({
      results: [{ preview_ref: "pv1.a.b", status: "SUPPORTED", reason_code: "OK", message: "", can_add: true }],
    });
    const screen = await render(
      <QueryClientProvider client={new QueryClient()}>
        <UiChart spec={spec} dataviz={{ threadId: "t", messageId: "m", contentId: "content-3" }} />
      </QueryClientProvider>,
    );
    await expect.element(screen.getByText("Revenue by salesperson")).toBeVisible();
    await vi.waitFor(() =>
      expect(screen.container.querySelector(".recharts-surface rect")).not.toBeNull(),
    );
    expect(screen.container.textContent).not.toContain("Read-only preview");
    await expect.element(screen.getByRole("button", { name: "Add to Dataviz" })).toBeVisible();
    // The whole stored block is located, never a guessed node inside it.
    expect(dataviz.check.mock.calls[0][0]).toEqual(
      [{ thread_id: "t", message_id: "m", content_id: "content-3" }],
    );
    await screen.getByText("View chart data").click();
    await expect.element(screen.getByRole("cell", { name: "3200", exact: true })).toBeVisible();
    expect(screen.getByRole("button", { name: "Add to Dataviz" }).elements()).toHaveLength(1);
  });

  it("keeps an unreadable spec visible as a message", async () => {
    const broken = await render(<UiChart spec="{" />);
    await expect.element(broken.getByText("This chart could not be displayed.")).toBeVisible();
  });
});
