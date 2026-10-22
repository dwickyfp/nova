import { useContext, useState } from "react";
import { useStateField } from "@openuidev/react-lang";
import { UiRuntimeContext } from "./runtime-context";
import { NodeArtifactButton } from "./artifact-button";
import { DataTable } from "./components";
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
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
type Props = Record<string, unknown>;
const str = (p: Props, key: string) => String(p[key] ?? "");
export default function DataChart({ p }: { p: Props }) {
  const [hiddenSeries, setHiddenSeries] = useState<string[]>([]);
  const [showData, setShowData] = useState(false);
  const selected = useStateField("selected", p.selected);
  const { readOnly } = useContext(UiRuntimeContext);
  const data = Array.isArray(p.data) ? (p.data as Props[]) : [];
  const x = str(p, "x"),
    y = str(p, "y");
  const series =
    Array.isArray(p.series) && p.series.length ? p.series.map(String) : [y];
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
    <section className="min-w-0" aria-label={str(p, "title") || "Chart"}>
      <div className="mb-2 flex min-h-7 items-center gap-2">
        <p className="min-w-0 flex-1 text-sm font-medium">{str(p, "title")}</p>
        <NodeArtifactButton
          data={p.data}
          noun="chart"
          draft={{
            kind: "chart",
            title: str(p, "title") || "Saved chart",
            chart: { type: String(p.type ?? "bar"), x, y, series },
          }}
        />
      </div>
      {series.length > 1 && !["pie", "donut", "scatter"].includes(String(p.type)) ? (
        <fieldset className="mb-2 flex flex-wrap gap-x-4 gap-y-1">
          <legend className="sr-only">Chart series</legend>
          {series.map((key, i) => (
            <label key={key} className="flex min-h-11 items-center gap-2 text-sm">
              <input type="checkbox" checked={!hiddenSeries.includes(key)} disabled={readOnly}
                onChange={() => setHiddenSeries((current) => current.includes(key)
                  ? current.filter((item) => item !== key) : [...current, key])} />
              <span aria-hidden="true" className="size-3 rounded-sm border border-foreground" style={{ background: color(i) }} />
              {key}
            </label>
          ))}
        </fieldset>
      ) : null}
      {data.length ? (
        <div className="h-64 min-w-0 [--ui-chart-edge:var(--foreground)] dark:[--ui-chart-edge:var(--card)]">
          <ResponsiveContainer width="100%" height="100%">
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
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          No rows match these filters.
        </p>
      )}
      {p.selected !== undefined && data.length ? <label className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        Select a result
        <select aria-label="Select chart result" className="min-h-11 rounded-md border border-input bg-background px-2" disabled={readOnly}
          value={typeof selected.value === "number" ? selected.value : -1} onChange={(event) => selected.setValue(Number(event.target.value))}>
          <option value={-1}>Choose a row</option>
          {data.map((row, index) => <option key={index} value={index}>{String(row[x] ?? index + 1)}</option>)}
        </select>
      </label> : null}
      <details
        className="mt-2"
        onToggle={(event) => setShowData(event.currentTarget.open)}
      >
        <summary className="min-h-11 cursor-pointer text-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring">
          View chart data
        </summary>
        {/* The grid is built on request, not for every chart in a long thread. */}
        {showData ? (
          <DataTable p={{ data, columns: [x, ...series], title: "Chart data" }} />
        ) : null}
      </details>
    </section>
  );
}
