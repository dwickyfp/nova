/**
 * A simple chart of a stored chart's rows (see `spec-chart.ts`), with its data
 * one click away. Loaded on demand: most turns hold no stored Vega chart.
 */
import { useState, type ReactNode } from "react";
import { ChartFrame } from "@/features/viz/chart-frame";
import { ResultTable } from "../../result-table";
import { cellOf, type SpecChart } from "./spec-chart";
import {
  Area,
  AreaChart,
  Bar,
  Brush,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
type Row = Record<string, unknown>;
// Business totals run to billions; a full number does not fit the axis gutter
// and loses its leading digits, so axis ticks read compactly (1.5B).
const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const axisTick = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? compact.format(value) : String(value);
export default function SpecChartView({
  plan: p,
  action,
}: {
  plan: Extract<SpecChart, { kind: "chart" }>;
  /** The header action, such as Add to Dataviz. */
  action?: ReactNode;
}) {
  const [hiddenSeries, setHiddenSeries] = useState<string[]>([]);
  const [showData, setShowData] = useState(false);
  const data: Row[] = p.rows;
  const x = p.x;
  const series = p.series;
  const y = series[0] ?? "";
  const color = (i: number) => `var(--chart-${(i % 8) + 1})`;
  const chartProps = {
    data,
    margin: { top: 8, right: 16, bottom: 8, left: 8 },
  };
  const axes = (
    <>
      <CartesianGrid stroke="var(--border)" />
      <XAxis
        dataKey={x}
        type={p.type === "scatter" ? "number" : "category"}
        stroke="var(--muted-foreground)"
        tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
      />
      <YAxis
        dataKey={p.type === "scatter" ? y : undefined}
        stroke="var(--muted-foreground)"
        tickFormatter={axisTick}
        tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
      />
      {data.length > 12 && p.type !== "scatter" ? <Brush dataKey={x} height={24} stroke="var(--muted-foreground)" fill="var(--card)" /> : null}
      <Tooltip
        itemStyle={{ color: "var(--foreground)" }}
        contentStyle={{
          background: "var(--popover)",
          borderColor: "var(--border)",
          color: "var(--foreground)",
        }}
      />
    </>
  );
  return (
    <section className="min-w-0 rounded-xl border bg-card p-4" aria-label={p.title || "Chart"}>
      <div className="mb-2 flex min-h-7 items-center gap-2">
        <p className="min-w-0 flex-1 truncate text-sm font-medium">{p.title || "Chart"}</p>
        {action}
      </div>
      {series.length > 1 && !["pie", "donut", "scatter"].includes(String(p.type)) ? (
        <fieldset className="mb-2 flex flex-wrap gap-x-4 gap-y-1">
          <legend className="sr-only">Chart series</legend>
          {series.map((key, i) => (
            <label key={key} className="flex min-h-11 items-center gap-2 text-sm">
              <input type="checkbox" checked={!hiddenSeries.includes(key)}
                onChange={() => setHiddenSeries((current) => current.includes(key)
                  ? current.filter((item) => item !== key) : [...current, key])} />
              <span aria-hidden="true" className="size-3 rounded-sm border border-foreground" style={{ background: color(i) }} />
              {key}
            </label>
          ))}
        </fieldset>
      ) : null}
      {data.length ? (
        <ChartFrame className="h-64 [--ui-chart-edge:var(--foreground)] dark:[--ui-chart-edge:var(--card)]">
            {p.type === "scatter" ? (
              <ScatterChart {...chartProps}>
                {axes}
                <Scatter
                  data={data}
                  fill={color(0)}
                  stroke="var(--ui-chart-edge)"
                  strokeWidth={2}
                  isAnimationActive={false}
                />
              </ScatterChart>
            ) : p.type === "line" ? (
              <LineChart {...chartProps}>
                {axes}
                {series.flatMap((key, i) => [
                  <Line
                    key={`${key}-edge`}
                    hide={hiddenSeries.includes(key)}
                    dataKey={key}
                    stroke="var(--ui-chart-edge)"
                    strokeWidth={5}
                    dot={false}
                    activeDot={false}
                    legendType="none"
                    tooltipType="none"
                    isAnimationActive={false}
                  />,
                  <Line
                    key={key}
                    hide={hiddenSeries.includes(key)}
                    dataKey={key}
                    stroke={color(i)}
                    strokeWidth={3}
                    dot={{
                      fill: color(i),
                      stroke: "var(--ui-chart-edge)",
                      strokeWidth: 2,
                    }}
                    isAnimationActive={false}
                  />,
                ])}
              </LineChart>
            ) : p.type === "area" ? (
              <AreaChart {...chartProps}>
                {axes}
                {series.map((key, i) => (
                  <Area
                    key={key}
                    hide={hiddenSeries.includes(key)}
                    dataKey={key}
                    stroke="var(--ui-chart-edge)"
                    strokeWidth={2}
                    fill={color(i)}
                    isAnimationActive={false}
                  />
                ))}
              </AreaChart>
            ) : p.type === "pie" || p.type === "donut" ? (
              <PieChart>
                <Tooltip
                  itemStyle={{ color: "var(--foreground)" }}
                  contentStyle={{
                    background: "var(--popover)",
                    color: "var(--foreground)",
                    borderColor: "var(--border)",
                  }}
                />
                <Pie
                  data={data}
                  dataKey={series[0]}
                  nameKey={x}
                  fill="var(--chart-1)"
                  stroke="var(--ui-chart-edge)"
                  strokeWidth={2}
                  innerRadius={p.type === "donut" ? "50%" : 0}
                  isAnimationActive={false}
                >
                  {data.map((_, i) => (
                    <Cell key={i} fill={color(i)} />
                  ))}
                </Pie>
              </PieChart>
            ) : (
              <BarChart {...chartProps}>
                {axes}
                {series.map((key, i) => (
                  <Bar
                    key={key}
                    hide={hiddenSeries.includes(key)}
                    dataKey={key}
                    fill={color(i)}
                    stroke="var(--ui-chart-edge)"
                    strokeWidth={2}
                    isAnimationActive={false}
                  />
                ))}
              </BarChart>
            )}
          </ChartFrame>
      ) : (
        <p className="text-sm text-muted-foreground">This chart has no rows.</p>
      )}
      <details
        className="mt-2"
        onToggle={(event) => setShowData(event.currentTarget.open)}
      >
        <summary className="min-h-11 cursor-pointer text-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring">
          View chart data
        </summary>
        {/* The grid is built on request, not for every chart in a long thread. */}
        {showData ? (
          <ResultTable
            block={{
              title: "Chart data",
              columns: [x, ...series],
              rows: data.map((row) => [x, ...series].map((column) => cellOf(row[column]))),
            }}
            note={null}
            heading={false}
          />
        ) : null}
      </details>
    </section>
  );
}
