import { expect, it } from "vitest";
import { render } from "vitest-browser-react";
import { Bar, BarChart } from "recharts";
import { ChartFrame } from "./chart-frame";
import "@/styles/index.css";

const data = [{ name: "A", value: 3 }, { name: "B", value: 5 }];

function Fixture({ width }: { width: number }) {
  return (
    <div style={{ width }}>
      <ChartFrame className="h-40">
        <BarChart data={data}>
          <Bar dataKey="value" isAnimationActive={false} />
        </BarChart>
      </ChartFrame>
    </div>
  );
}

it("draws at its measured size and keeps the chart through a collapsed box", async () => {
  const screen = await render(<Fixture width={480} />);
  const drawn = screen.container.querySelector("svg.recharts-surface");
  expect(drawn).not.toBeNull();
  expect(drawn?.getAttribute("width")).toBe("480");

  // A reflow can read the box 0 wide for a frame; the chart must not unmount.
  await screen.rerender(<Fixture width={0} />);
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(screen.container.querySelector("svg.recharts-surface")).toBe(drawn);

  await screen.rerender(<Fixture width={320} />);
  await expect
    .poll(() => screen.container.querySelector("svg.recharts-surface")?.getAttribute("width"))
    .toBe("320");
  expect(screen.container.querySelector("svg.recharts-surface")).toBe(drawn);
});
