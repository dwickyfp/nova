import { expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { ChartBlock } from "./chart-block";

it("renders a sanitized chart with the real Vega runtime", async () => {
  const screen = await render(
    <ChartBlock
      spec={JSON.stringify({
        mark: "bar",
        data: { values: [{ region: "West", sales: 12 }] },
        encoding: {
          x: { field: "region", type: "nominal" },
          y: { field: "sales", type: "quantitative" },
        },
      })}
    />,
  );

  await vi.waitFor(
    () => {
      const svg = screen
        .getByTestId("vega-chart-container")
        .element()
        .querySelector("svg");
      expect(svg).not.toBeNull();
    },
    { timeout: 10_000 },
  );
  screen.unmount();
});
