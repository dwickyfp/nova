import { useMemo } from "react";
import { ChartBlock } from "@/features/agents/chart-block";
import { readToken } from "@/lib/read-token";
import { calendarDay, formatValue, humanize } from "./format";
import type { Story } from "./newspaper-api";

const day = (value: string) =>
  calendarDay(value).toLocaleDateString("en", { day: "numeric", month: "short" });

/**
 * The story's metric by day: one line, the typical level it is compared with,
 * and the days that formed that comparison. One series, so it needs no legend.
 */
export function TrendChart({ story, height = 260 }: { story: Story; height?: number }) {
  const spec = useMemo(() => {
    const series = readToken("--chart-4", "#4f7ee8");
    const muted = readToken("--muted-foreground", "#71717a");
    const surface = readToken("--background", "#ffffff");
    const baseline = new Set(story.baseline_dates);
    const values = story.series.map((point) => ({
      date: point.date,
      value: point.value,
      label: day(point.date),
      figure: formatValue(point.value, story.unit),
      role:
        point.date === story.edition_date
          ? "This edition"
          : baseline.has(point.date)
            ? "Compared day"
            : "",
    }));
    const tooltip = [
      { field: "label", title: "Day" },
      { field: "figure", title: story.metric_label },
    ];
    const position = {
      x: {
        field: "date",
        type: "temporal",
        axis: { title: null, format: "%d %b", grid: false, tickCount: 6 },
      },
      y: {
        field: "value",
        type: "quantitative",
        axis: { title: null, format: "~s", tickCount: 4 },
        scale: { zero: false, nice: true },
      },
    };
    return JSON.stringify({
      $schema: "https://vega.github.io/schema/vega-lite/v6.json",
      height,
      data: { values },
      layer: [
        {
          data: { values: [{ level: story.before }] },
          mark: { type: "rule", color: muted, strokeDash: [4, 4], strokeWidth: 1 },
          encoding: { y: { field: "level", type: "quantitative" } },
        },
        { mark: { type: "line", color: series, strokeWidth: 2 }, encoding: position },
        {
          transform: [{ filter: "datum.role === 'Compared day'" }],
          mark: {
            type: "point",
            color: series,
            fill: surface,
            size: 70,
            strokeWidth: 2,
            opacity: 1,
          },
          encoding: { ...position, tooltip },
        },
        {
          transform: [{ filter: "datum.role === 'This edition'" }],
          mark: {
            type: "point",
            color: series,
            filled: true,
            size: 130,
            stroke: surface,
            strokeWidth: 2,
            opacity: 1,
          },
          encoding: { ...position, tooltip },
        },
        {
          mark: { type: "point", size: 400, opacity: 0 },
          encoding: { ...position, tooltip },
        },
      ],
    });
  }, [story, height]);
  const weekday = calendarDay(story.edition_date).toLocaleDateString("en", {
    weekday: "long",
  });
  return (
    <figure className="min-w-0">
      <ChartBlock spec={spec} />
      <figcaption className="mt-2 text-xs text-muted-foreground">
        {story.metric_label} by day. The dashed line is the typical {weekday} (
        {formatValue(story.before, story.unit)}); open circles are the{" "}
        {story.baseline_dates.length} earlier {weekday}s it is compared with.
      </figcaption>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-muted-foreground focus-visible:outline focus-visible:outline-ring">
          Show the figures
        </summary>
        <div className="mt-2 max-h-64 overflow-auto rounded-md border">
          <table className="w-full text-left tabular-nums">
            <thead className="text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Day</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  {story.metric_label}
                </th>
              </tr>
            </thead>
            <tbody>
              {story.series.map((point) => (
                <tr key={point.date} className="border-t">
                  <td className="px-3 py-1.5">{day(point.date)}</td>
                  <td className="px-3 py-1.5 text-right">
                    {formatValue(point.value, story.unit)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

/** Change by segment for a whole-view story, largest movement first. */
export function DriverChart({ story }: { story: Story }) {
  const spec = useMemo(() => {
    const values = story.drivers.map((driver) => ({
      segment: driver.value,
      change: driver.change,
      figure: formatValue(driver.change, story.unit),
    }));
    return JSON.stringify({
      $schema: "https://vega.github.io/schema/vega-lite/v6.json",
      height: Math.max(120, values.length * 30),
      data: { values },
      mark: {
        type: "bar",
        color: readToken("--chart-4", "#4f7ee8"),
        cornerRadiusEnd: 4,
        height: { band: 0.6 },
      },
      encoding: {
        y: {
          field: "segment",
          type: "nominal",
          sort: null,
          axis: { title: null, ticks: false, domain: false },
        },
        x: {
          field: "change",
          type: "quantitative",
          axis: { title: null, format: "~s", tickCount: 4 },
        },
        tooltip: [
          { field: "segment", title: humanize(story.drivers[0]?.dimension ?? "Segment") },
          { field: "figure", title: "Change" },
        ],
      },
    });
  }, [story]);
  if (!story.drivers.length) return null;
  return (
    <figure className="min-w-0">
      <ChartBlock spec={spec} />
      <figcaption className="mt-2 text-xs text-muted-foreground">
        Change against the typical day by {humanize(story.drivers[0].dimension).toLowerCase()}.
        This shows where the movement is, not what caused it.
      </figcaption>
    </figure>
  );
}

/** A small trend for a story card; decorative, the card states the figures. */
export function Sparkline({ story }: { story: Story }) {
  const points = story.series;
  if (points.length < 2) return null;
  const values = points.map((point) => point.value);
  const low = Math.min(...values);
  const span = Math.max(...values) - low || 1;
  const at = (index: number, value: number) =>
    `${((index / (points.length - 1)) * 116 + 2).toFixed(1)},${(30 - ((value - low) / span) * 26).toFixed(1)}`;
  const last = at(points.length - 1, values[values.length - 1]).split(",");
  return (
    <svg
      viewBox="0 0 120 32"
      aria-hidden="true"
      className="h-8 w-28 shrink-0 text-chart-4"
      preserveAspectRatio="none"
    >
      <polyline
        points={values.map((value, index) => at(index, value)).join(" ")}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={last[0]} cy={last[1]} r="2.5" fill="currentColor" />
    </svg>
  );
}
