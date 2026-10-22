import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { UiChart } from "./ui-chart";
import "@/styles/index.css";

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
  it("renders with the intelligent UI components and saves only the chart", async () => {
    const onSave = vi.fn();
    const screen = await render(<UiChart spec={spec} onSave={onSave} />);
    await expect.element(screen.getByText("Revenue by salesperson")).toBeVisible();
    await vi.waitFor(() =>
      expect(screen.container.querySelector(".recharts-surface rect")).not.toBeNull(),
    );
    expect(screen.container.textContent).not.toContain("Read-only preview");
    await screen.getByRole("button", { name: "Save chart as artifact" }).click();
    expect(onSave).toHaveBeenCalledOnce();
    await screen.getByText("View chart data").click();
    await expect.element(screen.getByRole("cell", { name: "3200", exact: true })).toBeVisible();
    expect(screen.getByRole("button", { name: "Save table as artifact" }).query()).toBeNull();
  });

  it("marks a saved chart and keeps an unreadable spec visible as a message", async () => {
    const saved = await render(<UiChart spec={spec} onSave={() => {}} saved />);
    await expect
      .element(saved.getByRole("button", { name: "Artifact saved" }))
      .toBeDisabled();
    const broken = await render(<UiChart spec="{" />);
    await expect.element(broken.getByText("This chart could not be displayed.")).toBeVisible();
  });
});
