import { useCallback } from "react";
import { EChart, type ChartTokens } from "./echart";
import { calendarDay, formatValue, humanize } from "./format";
import type { ChartKind } from "./chart-kind";
import type { Story } from "./newspaper-api";

const day = (value: string) =>
  calendarDay(value).toLocaleDateString("en", { day: "numeric", month: "short" });
const compact = (value: number) =>
  new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);

function tooltip(t: ChartTokens) {
  return {
    backgroundColor: t.surface,
    borderColor: t.border,
    borderWidth: 1,
    padding: [8, 10],
    textStyle: { color: t.text, fontSize: 12, fontFamily: "inherit" },
    extraCssText: "box-shadow:none;border-radius:8px;",
  };
}

/**
 * The story's metric by day: one line, the typical level it is compared with,
 * the days that formed that comparison, and the edition day. One series, so the
 * caption names it and no legend is drawn.
 */
export function TrendChart({
  story,
  height = 260,
  detailed = true,
}: {
  story: Story;
  height?: number;
  /** A compact chart drops the caption and the figures table. */
  detailed?: boolean;
}) {
  const build = useCallback(
    (t: ChartTokens) => {
      const compared = new Set(story.baseline_dates);
      return {
        animation: false,
        textStyle: { fontFamily: "inherit" },
        grid: { left: 4, right: 16, top: 16, bottom: 4, containLabel: true },
        tooltip: {
          ...tooltip(t),
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: t.border } },
          formatter: (items: { dataIndex: number }[]) => {
            const point = story.series[items[0].dataIndex];
            const role =
              point.date === story.edition_date
                ? " · this edition"
                : compared.has(point.date)
                  ? " · compared day"
                  : "";
            return `${day(point.date)}${role}<br/><strong>${formatValue(point.value, story.unit)}</strong>`;
          },
        },
        xAxis: {
          type: "category",
          boundaryGap: false,
          data: story.series.map((point) => day(point.date)),
          axisLine: { lineStyle: { color: t.border } },
          axisTick: { show: false },
          axisLabel: { color: t.muted, fontSize: 11, interval: 6, hideOverlap: true },
        },
        yAxis: {
          type: "value",
          scale: true,
          splitNumber: 3,
          axisLabel: { color: t.muted, fontSize: 11, formatter: compact },
          splitLine: { lineStyle: { color: t.border, opacity: 0.6 } },
        },
        series: [
          {
            type: "line",
            data: story.series.map((point) => ({
              value: point.value,
              // The edition day carries its own figure, so the chart reads without a hover.
              label:
                point.date === story.edition_date
                  ? {
                      show: true,
                      position: "left",
                      distance: 8,
                      color: t.text,
                      fontSize: 12,
                      fontWeight: 600,
                      formatter: compact(point.value),
                    }
                  : { show: false },
              symbol: "circle",
              symbolSize:
                point.date === story.edition_date ? 11 : compared.has(point.date) ? 8 : 0,
              itemStyle:
                point.date === story.edition_date
                  ? { color: t.series, borderColor: t.surface, borderWidth: 2 }
                  : { color: t.surface, borderColor: t.series, borderWidth: 2 },
            })),
            showSymbol: true,
            lineStyle: { color: t.series, width: 2 },
            areaStyle: { color: t.series, opacity: 0.06 },
            emphasis: { disabled: true },
            markLine: {
              silent: true,
              symbol: "none",
              label: { show: false },
              lineStyle: { color: t.muted, type: [4, 4], width: 1 },
              data: [{ yAxis: story.before }],
            },
          },
        ],
      };
    },
    [story],
  );
  const weekday = calendarDay(story.edition_date).toLocaleDateString("en", {
    weekday: "long",
  });
  const label = `${story.metric_label} by day, ending ${day(story.edition_date)} at ${formatValue(story.after, story.unit)} against a typical ${weekday} of ${formatValue(story.before, story.unit)}`;
  if (!detailed) return <EChart build={build} height={height} label={label} />;
  return (
    <figure className="min-w-0">
      <EChart build={build} height={height} label={label} />
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
export function DriverChart({ story, caption = true }: { story: Story; caption?: boolean }) {
  const build = useCallback(
    (t: ChartTokens) => {
      const drivers = [...story.drivers].reverse();
      return {
        animation: false,
        textStyle: { fontFamily: "inherit" },
        grid: { left: 4, right: 16, top: 4, bottom: 4, containLabel: true },
        tooltip: {
          ...tooltip(t),
          trigger: "item",
          formatter: (item: { dataIndex: number }) => {
            const driver = drivers[item.dataIndex];
            return `${driver.value}<br/><strong>${formatValue(driver.change, story.unit)}</strong>`;
          },
        },
        xAxis: {
          type: "value",
          splitNumber: 3,
          axisLabel: { color: t.muted, fontSize: 11, formatter: compact },
          splitLine: { lineStyle: { color: t.border, opacity: 0.6 } },
        },
        yAxis: {
          type: "category",
          data: drivers.map((driver) => driver.value),
          axisLine: { lineStyle: { color: t.border } },
          axisTick: { show: false },
          axisLabel: { color: t.text, fontSize: 12 },
        },
        series: [
          {
            type: "bar",
            data: drivers.map((driver) => ({
              value: driver.change,
              itemStyle: {
                color: t.series,
                borderRadius: driver.change >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4],
              },
            })),
            barWidth: 14,
          },
        ],
      };
    },
    [story],
  );
  if (!story.drivers.length) return null;
  const dimension = humanize(story.drivers[0].dimension).toLowerCase();
  return (
    <figure className="min-w-0">
      <EChart
        build={build}
        height={Math.max(120, story.drivers.length * 30)}
        label={`Change against the typical day by ${dimension}`}
      />
      {caption && (
        <figcaption className="mt-2 text-xs text-muted-foreground">
          Change against the typical day by {dimension}. This shows where the
          movement is, not what caused it.
        </figcaption>
      )}
    </figure>
  );
}

/**
 * The edition day beside the same weekday of earlier weeks: the comparison the
 * story is built on, shown as it was made.
 */
export function WeekdayChart({ story, height = 150 }: { story: Story; height?: number }) {
  const build = useCallback(
    (t: ChartTokens) => {
      const values = new Map(story.series.map((point) => [point.date, point.value]));
      const days = [...story.baseline_dates].sort().concat(story.edition_date);
      return {
        animation: false,
        textStyle: { fontFamily: "inherit" },
        grid: { left: 4, right: 12, top: 20, bottom: 4, containLabel: true },
        tooltip: {
          ...tooltip(t),
          trigger: "item",
          formatter: (item: { dataIndex: number }) =>
            `${day(days[item.dataIndex])}<br/><strong>${formatValue(values.get(days[item.dataIndex]) ?? 0, story.unit)}</strong>`,
        },
        xAxis: {
          type: "category",
          data: days.map(day),
          axisLine: { lineStyle: { color: t.border } },
          axisTick: { show: false },
          axisLabel: { color: t.muted, fontSize: 11 },
        },
        yAxis: {
          type: "value",
          splitNumber: 3,
          axisLabel: { color: t.muted, fontSize: 11, formatter: compact },
          splitLine: { lineStyle: { color: t.border, opacity: 0.6 } },
        },
        series: [
          {
            type: "bar",
            barWidth: "46%",
            data: days.map((date) => ({
              value: values.get(date) ?? 0,
              itemStyle: {
                color: t.series,
                opacity: date === story.edition_date ? 1 : 0.32,
                borderRadius: [4, 4, 0, 0],
              },
              label: {
                show: date === story.edition_date,
                position: "top",
                color: t.text,
                fontSize: 12,
                fontWeight: 600,
                formatter: compact(values.get(date) ?? 0),
              },
            })),
            markLine: {
              silent: true,
              symbol: "none",
              label: { show: false },
              lineStyle: { color: t.muted, type: [4, 4], width: 1 },
              data: [{ yAxis: story.before }],
            },
          },
        ],
      };
    },
    [story],
  );
  return (
    <EChart
      build={build}
      height={height}
      label={`${story.metric_label} on ${day(story.edition_date)} beside the same weekday of ${story.baseline_dates.length} earlier weeks`}
    />
  );
}

/** Observed against typical as two bars on one scale. */
export function LevelChart({ story, height = 110 }: { story: Story; height?: number }) {
  const build = useCallback(
    (t: ChartTokens) => ({
      animation: false,
      textStyle: { fontFamily: "inherit" },
      grid: { left: 68, right: 60, top: 4, bottom: 4 },
      tooltip: { show: false },
      xAxis: { type: "value", show: false, min: 0 },
      yAxis: {
        type: "category",
        data: ["Typical", "Observed"],
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: t.muted, fontSize: 12 },
      },
      series: [
        {
          type: "bar",
          barWidth: 16,
          data: [story.before, story.after].map((value, index) => ({
            value,
            itemStyle: {
              color: t.series,
              opacity: index === 1 ? 1 : 0.32,
              borderRadius: [0, 4, 4, 0],
            },
            label: {
              show: true,
              position: "right",
              color: t.text,
              fontSize: 12,
              fontWeight: index === 1 ? 600 : 400,
              formatter: compact(value),
            },
          })),
        },
      ],
    }),
    [story],
  );
  return (
    <EChart
      build={build}
      height={height}
      label={`${story.metric_label}: observed ${formatValue(story.after, story.unit)} against typical ${formatValue(story.before, story.unit)}`}
    />
  );
}

export function StoryChart({ story, kind }: { story: Story; kind: ChartKind }) {
  return (
    <div data-chart-kind={kind} className="min-w-0">
      {kind === "weekday" ? (
        <WeekdayChart story={story} />
      ) : kind === "level" ? (
        <LevelChart story={story} />
      ) : kind === "drivers" ? (
        <DriverChart story={story} caption={false} />
      ) : (
        <TrendChart story={story} height={150} detailed={false} />
      )}
    </div>
  );
}
