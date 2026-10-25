import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { cn } from "@/lib/utils";
import { formatNumber, measureCaption } from "./insight";
import type { PresModel } from "./types";

/**
 * VizView draws a presentation model the viz core computed: marks
 * (bar, line, area, circle, pie, text) with their encodings, on Nova tokens.
 * It never fetches and never parses SQL; it renders what the core decided.
 *
 * Data draws in the neutral --viz-mark/--viz-line; only the mark the view's
 * answer is about (``highlight``) takes --viz-highlight, so the accent points
 * at the finding. Categorical color encodings keep the chart palette, where
 * hue is the data.
 */

const seriesColor = (index: number) => `var(--chart-${(index % 8) + 1})`;

/** Abbreviated number format: 1.2K, 3.37T, 0.15. */
export function formatMeasure(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "–";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return formatNumber(numeric);
}

type CellValue = string | number | null;

export type MarkHighlight = { dimension: string; value: CellValue } | null;

function toDisplay(row: (string | number | null)[], index: number): CellValue {
  return index < row.length ? row[index] : null;
}

export function VizView({
  presmodel,
  compact = false,
  thumbnail = false,
  className,
  onMarkClick,
  selectedMark,
  highlight = null,
}: {
  presmodel: PresModel;
  compact?: boolean;
  /** Fill the parent with marks only (no axes): library thumbnails. */
  thumbnail?: boolean;
  className?: string;
  onMarkClick?: (dimensionValues: Record<string, CellValue>) => void;
  selectedMark?: Record<string, CellValue> | null;
  /** The mark the view's answer is about, drawn in the highlight color. */
  highlight?: MarkHighlight;
}) {
  const geometry = useMemo(() => buildGeometry(presmodel), [presmodel]);
  if (!presmodel.rows.length) {
    return (
      <p className={cn("type-meta py-8 text-center", className)}>
        The worksheet returned no marks.
      </p>
    );
  }
  // Several measures on an axis mark get one pane each, sharing the
  // dimension axis, so measures on different scales stay readable.
  const panes =
    PANED_MARKS.has(presmodel.mark) && geometry.dimensions.length > 0 && geometry.series.length > 1
      ? geometry.series.map((series) => ({ ...geometry, series: [series] }))
      : [geometry];
  return (
    <section
      className={cn("viz-numerals min-w-0", thumbnail ? "flex h-full flex-col gap-1" : "flex flex-col gap-4", className)}
      aria-label={presmodel.title || "Chart"}
    >
      {panes.map((pane) => (
        <div
          key={pane.series.map((series) => series.name).join("|")}
          className={cn("min-w-0", thumbnail && "min-h-0 flex-1")}
        >
          {panes.length > 1 && !thumbnail ? (
            <p className="type-label mb-1 px-1 text-ink-secondary">{measureCaption(pane.series[0].name)}</p>
          ) : null}
          <Marks
            presmodel={presmodel}
            geometry={pane}
            compact={compact || panes.length > 2}
            thumbnail={thumbnail}
            onMarkClick={onMarkClick}
            selectedMark={selectedMark ?? null}
            highlight={highlight}
          />
        </div>
      ))}
    </section>
  );
}

const PANED_MARKS: ReadonlySet<string> = new Set(["bar", "line", "area", "tick", "square"]);

/** Ranked horizontal bars read best up to this many categories. */
const MAX_RANKED_BARS = 24;

type Geometry = {
  xField: string;
  xType: "category" | "number";
  series: { name: string; index: number }[];
  colorField: string | null;
  colorKind: "categorical" | "quantitative" | null;
  colorDomain: (string | number)[];
  chartData: Record<string, unknown>[];
  dimensions: { name: string; index: number }[];
  measures: { name: string; index: number }[];
  temporalAxis: boolean;
};

const COMPOSITE_AXIS = "__axis";

function buildGeometry(presmodel: PresModel): Geometry {
  const dimensions = presmodel.columns
    .map((column, index) => ({ name: column.name, index }))
    .filter((item) => presmodel.columns[item.index].role === "dimension");
  const measures = presmodel.columns
    .map((column, index) => ({ name: column.name, index }))
    .filter((item) => presmodel.columns[item.index].role === "measure");

  const legend = presmodel.legend.color;
  const colorField = legend?.field ?? null;
  const colorIsDimension = dimensions.some((dimension) => dimension.name === colorField);
  const axisDimensions = dimensions.filter((dimension) => dimension.name !== colorField);
  const axisDimension = axisDimensions[0] ?? dimensions[0];
  // Several dimensions on the axis read as nested headers: "East · NY".
  const composite = axisDimensions.length > 1;

  const series = measures.map((measure) => ({ name: measure.name, index: measure.index }));
  const xField = composite
    ? COMPOSITE_AXIS
    : axisDimension
      ? axisDimension.name
      : measures[0]?.name ?? "value";
  const xType = axisDimension || presmodel.axes.x?.discrete !== false ? "category" : "number";
  const axisColumn = axisDimension ? presmodel.columns[axisDimension.index] : null;

  const chartData = presmodel.rows.map((row) => {
    const entry: Record<string, unknown> = {};
    for (const dimension of dimensions) {
      entry[dimension.name] = toDisplay(row, dimension.index);
    }
    if (composite) {
      entry[COMPOSITE_AXIS] = axisDimensions
        .map((dimension) => {
          const value = toDisplay(row, dimension.index);
          return value === null ? "Null" : String(value);
        })
        .join(" · ");
    }
    for (const measure of measures) {
      entry[measure.name] = numericOr(toDisplay(row, measure.index));
    }
    return entry;
  });

  return {
    xField,
    xType: presmodel.mark === "circle" && !axisDimension ? "number" : xType,
    series,
    colorField,
    colorKind: colorField ? (colorIsDimension ? "categorical" : "quantitative") : null,
    colorDomain: legend?.domain ?? [],
    chartData,
    dimensions,
    measures,
    temporalAxis: Boolean(axisColumn && (axisColumn.field_type === "temporal" || axisColumn.date_part)),
  };
}

function numericOr(value: CellValue): number | null {
  if (value === null) return null;
  if (typeof value === "number") return value;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function colorForIndex(geometry: Geometry, index: number): string {
  if (geometry.colorKind === "categorical" && geometry.colorField) {
    const position = geometry.colorDomain.indexOf(geometry.colorDomain[index]);
    return seriesColor(Math.max(0, position));
  }
  return seriesColor(index);
}

function average(geometry: Geometry, seriesName: string): number | null {
  const values = geometry.chartData
    .map((entry) => entry[seriesName])
    .filter((value): value is number => typeof value === "number");
  return values.length > 1 ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function Marks({
  presmodel,
  geometry,
  compact,
  thumbnail = false,
  onMarkClick,
  selectedMark,
  highlight,
}: {
  presmodel: PresModel;
  geometry: Geometry;
  compact: boolean;
  thumbnail?: boolean;
  onMarkClick?: (values: Record<string, CellValue>) => void;
  selectedMark: Record<string, CellValue> | null;
  highlight: MarkHighlight;
}) {
  const mark = presmodel.mark;
  const height = thumbnail ? "h-full" : compact ? "h-40" : "h-64";
  const tick = { fontSize: 12, fill: "var(--muted-foreground)" };
  const valueFormat = (value: string | number) =>
    typeof value === "number" ? formatMeasure(value) : String(value);
  const axisProps = {
    hide: thumbnail,
    stroke: "var(--viz-axis)",
    tickLine: false,
    tick,
    tickFormatter: valueFormat,
  };
  const tooltip = (
    <Tooltip
      cursor={{ fill: "var(--viz-grid)" }}
      itemStyle={{ color: "var(--foreground)" }}
      contentStyle={{
        background: "var(--popover)",
        borderColor: "var(--border)",
        borderRadius: 6,
        color: "var(--foreground)",
        fontSize: 12,
      }}
      formatter={(value) => (typeof value === "number" ? formatMeasure(value) : value)}
    />
  );
  const neutral = geometry.colorKind !== "categorical";
  const isHighlighted = (entry: Record<string, unknown>) =>
    Boolean(
      highlight &&
        (entry[highlight.dimension] === highlight.value ||
          (geometry.xField === COMPOSITE_AXIS && entry[COMPOSITE_AXIS] === highlight.value)),
    );
  const clickMark = (payload?: Record<string, unknown>) => {
    if (!onMarkClick || !payload) return;
    const values: Record<string, CellValue> = {};
    for (const dimension of geometry.dimensions) {
      values[dimension.name] = (payload[dimension.name] as CellValue) ?? null;
    }
    onMarkClick(values);
  };
  const grid = thumbnail ? null : <CartesianGrid stroke="var(--viz-grid)" vertical={false} />;

  if (mark === "text") {
    return (
      <TextTable presmodel={presmodel} geometry={geometry} onMarkClick={onMarkClick} selectedMark={selectedMark} />
    );
  }

  if (mark === "pie") {
    const valueKey = geometry.series[0]?.name ?? "value";
    const nameKey = geometry.dimensions[0]?.name ?? "name";
    return (
      <div className={cn(height, "min-w-0")}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            {tooltip}
            <Pie
              data={geometry.chartData}
              dataKey={valueKey}
              nameKey={nameKey}
              innerRadius="55%"
              outerRadius="85%"
              stroke="var(--card)"
              strokeWidth={2}
              isAnimationActive={false}
            >
              {geometry.chartData.map((entry, index) => (
                <Cell
                  key={index}
                  fill={
                    neutral
                      ? isHighlighted(entry)
                        ? "var(--viz-highlight)"
                        : "var(--viz-mark)"
                      : colorForIndex(geometry, index)
                  }
                />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
      </div>
    );
  }

  if (mark === "circle" || mark === "square") {
    const xAxisMeasure = geometry.measures[0];
    const yAxisMeasure = geometry.measures[1] ?? geometry.measures[0];
    if (!xAxisMeasure || !yAxisMeasure) {
      return <FallbackGrid presmodel={presmodel} />;
    }
    const scatterData = presmodel.rows.map((row, index) => ({
      x: numericOr(toDisplay(row, xAxisMeasure.index)),
      y: numericOr(toDisplay(row, yAxisMeasure.index)),
      name: geometry.dimensions
        .map((dimension) => toDisplay(row, dimension.index))
        .filter((value) => value !== null)
        .join(" · "),
      fill: neutral ? "var(--viz-line)" : colorForIndex(geometry, index),
    }));
    return (
      <div className={cn(height, "min-w-0")}>
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
            {thumbnail ? null : <CartesianGrid stroke="var(--viz-grid)" />}
            <XAxis dataKey="x" type="number" name={measureCaption(xAxisMeasure.name)} {...axisProps} />
            <YAxis dataKey="y" type="number" name={measureCaption(yAxisMeasure.name)} {...axisProps} width={56} />
            <ZAxis range={[48, 48]} />
            {tooltip}
            <Scatter
              data={scatterData}
              fillOpacity={0.8}
              isAnimationActive={false}
              shape={mark === "square" ? "square" : "circle"}
            >
              {scatterData.map((point, index) => (
                <Cell key={index} fill={point.fill} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    );
  }

  const shared = { data: geometry.chartData, margin: { top: 12, right: 24, bottom: 4, left: 4 } };
  const xAxis = (
    <XAxis
      dataKey={geometry.xField}
      type={geometry.xType === "number" ? "number" : "category"}
      minTickGap={16}
      {...axisProps}
    />
  );
  const yAxis = <YAxis {...axisProps} axisLine={false} width={56} />;
  const single = geometry.series.length === 1 && neutral;
  const mean = single ? average(geometry, geometry.series[0].name) : null;
  const meanLine =
    mean !== null && !thumbnail ? (
      <ReferenceLine
        y={mean}
        stroke="var(--viz-reference)"
        strokeDasharray="4 4"
        label={{ value: `avg ${formatMeasure(mean)}`, position: "insideTopRight", fill: "var(--muted-foreground)", fontSize: 12 }}
      />
    ) : null;

  if (mark === "line" || mark === "area") {
    const highlighted = single ? geometry.chartData.find(isHighlighted) : undefined;
    const Chart = mark === "line" ? LineChart : AreaChart;
    return (
      <div className={cn(height, "min-w-0")}>
        <ResponsiveContainer width="100%" height="100%">
          <Chart {...shared}>
            {grid}
            {xAxis}
            {yAxis}
            {tooltip}
            {meanLine}
            {geometry.series.map((series, index) => {
              const color = neutral ? "var(--viz-line)" : colorForIndex(geometry, index);
              return mark === "line" ? (
                <Line
                  key={series.name}
                  dataKey={series.name}
                  name={measureCaption(series.name)}
                  stroke={color}
                  strokeWidth={1.75}
                  dot={false}
                  activeDot={{ r: 4, fill: color, strokeWidth: 0 }}
                  isAnimationActive={false}
                />
              ) : (
                <Area
                  key={series.name}
                  dataKey={series.name}
                  name={measureCaption(series.name)}
                  stroke={color}
                  fill={color}
                  fillOpacity={0.14}
                  strokeWidth={1.75}
                  isAnimationActive={false}
                />
              );
            })}
            {highlighted && !thumbnail ? (
              <ReferenceDot
                x={highlighted[geometry.xField] as string | number}
                y={highlighted[geometry.series[0].name] as number}
                r={5}
                fill="var(--viz-highlight)"
                stroke="var(--card)"
                strokeWidth={2}
              />
            ) : null}
          </Chart>
        </ResponsiveContainer>
      </div>
    );
  }

  // A single measure over a handful of categories reads as a ranked list:
  // horizontal bars with the category on the left and the value at the end.
  const ranked =
    mark === "bar" &&
    single &&
    !geometry.temporalAxis &&
    geometry.dimensions.length > 0 &&
    geometry.chartData.length <= MAX_RANKED_BARS;
  if (ranked) {
    const series = geometry.series[0];
    const rowHeight = compact ? 24 : 32;
    const chartHeight = thumbnail ? undefined : geometry.chartData.length * rowHeight + 36;
    return (
      <div className={cn("min-w-0", thumbnail && "h-full")} style={chartHeight ? { height: chartHeight } : undefined}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart layout="vertical" data={geometry.chartData} margin={{ top: 4, right: 56, bottom: 4, left: 4 }}>
            {thumbnail ? null : <CartesianGrid stroke="var(--viz-grid)" horizontal={false} />}
            <XAxis type="number" {...axisProps} />
            <YAxis
              type="category"
              dataKey={geometry.xField}
              {...axisProps}
              axisLine={false}
              width={thumbnail ? 0 : 120}
              tick={{ ...tick, fill: "var(--ink-secondary)", fontSize: 13 }}
            />
            {tooltip}
            {mean !== null && !thumbnail ? (
              <ReferenceLine
                x={mean}
                stroke="var(--viz-reference)"
                strokeDasharray="4 4"
                label={{ value: `avg ${formatMeasure(mean)}`, position: "top", fill: "var(--muted-foreground)", fontSize: 12 }}
              />
            ) : null}
            <Bar
              dataKey={series.name}
              name={measureCaption(series.name)}
              radius={[0, 2, 2, 0]}
              barSize={compact ? 14 : 20}
              isAnimationActive={false}
              cursor={onMarkClick ? "pointer" : undefined}
              onClick={(data: { payload?: Record<string, unknown> }) => clickMark(data?.payload)}
            >
              {geometry.chartData.map((entry, index) => (
                <Cell key={index} fill={isHighlighted(entry) ? "var(--viz-highlight)" : "var(--viz-mark)"} />
              ))}
              {thumbnail ? null : (
                <LabelList
                  dataKey={series.name}
                  position="right"
                  formatter={(value: unknown) => formatMeasure(value as number)}
                  style={{ fill: "var(--foreground)", fontSize: 12 }}
                />
              )}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // Columns (time on the axis, or many categories) and ticks.
  return (
    <div className={cn(height, "min-w-0")}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart {...shared}>
          {grid}
          {xAxis}
          {yAxis}
          {tooltip}
          {meanLine}
          {geometry.series.map((series, index) => (
            <Bar
              key={series.name}
              dataKey={series.name}
              name={measureCaption(series.name)}
              fill={neutral ? "var(--viz-mark)" : colorForIndex(geometry, index)}
              radius={mark === "bar" ? [2, 2, 0, 0] : undefined}
              barSize={mark === "tick" ? 3 : undefined}
              isAnimationActive={false}
              cursor={onMarkClick ? "pointer" : undefined}
              onClick={(data: { payload?: Record<string, unknown> }) => clickMark(data?.payload)}
            >
              {geometry.chartData.map((entry, cellIndex) => (
                <Cell
                  key={cellIndex}
                  fill={
                    !neutral
                      ? colorForIndex(geometry, cellIndex)
                      : single && isHighlighted(entry)
                        ? "var(--viz-highlight)"
                        : "var(--viz-mark)"
                  }
                />
              ))}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** The text mark: a crosstab, shaded like a highlight table when a color
 * measure is present (a heat map). */
function TextTable({
  presmodel,
  geometry,
  onMarkClick,
  selectedMark,
}: {
  presmodel: PresModel;
  geometry: Geometry;
  onMarkClick?: (values: Record<string, CellValue>) => void;
  selectedMark: Record<string, CellValue> | null;
}) {
  const colorMeasure = geometry.series.find((series) => series.name === geometry.colorField);
  const values = colorMeasure
    ? presmodel.rows
        .map((row) => numericOr(toDisplay(row, colorMeasure.index)))
        .filter((value): value is number => value !== null)
    : [];
  const extent = values.length ? { min: Math.min(...values), max: Math.max(...values) } : null;
  const shade = (value: number | null): string | null => {
    if (!extent || value === null || extent.max === extent.min) return null;
    const ratio = (value - extent.min) / (extent.max - extent.min);
    return `color-mix(in oklab, var(--viz-line) ${Math.round(10 + ratio * 50)}%, transparent)`;
  };
  const isSelected = (row: (string | number | null)[]) => {
    if (!selectedMark) return false;
    return geometry.dimensions.every(
      (dimension) =>
        selectedMark[dimension.name] === undefined ||
        selectedMark[dimension.name] === toDisplay(row, dimension.index),
    );
  };
  return (
    <div className="min-w-0 overflow-x-auto">
      <table className="type-ui w-full min-w-64 border-collapse">
        <thead>
          <tr className="border-b">
            {presmodel.columns.map((column) => (
              <th
                key={column.name}
                scope="col"
                className={cn("type-label px-3 py-2 text-left text-ink-secondary", column.role === "measure" && "text-right")}
              >
                {column.role === "measure" ? measureCaption(column.name) : column.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {presmodel.rows.map((row, rowIndex) => (
            <tr
              key={rowIndex}
              className={cn(
                "border-b border-viz-grid last:border-b-0",
                onMarkClick && "cursor-pointer hover:bg-surface-1",
                isSelected(row) && "bg-surface-1",
              )}
              onClick={() => {
                if (!onMarkClick) return;
                const picked: Record<string, CellValue> = {};
                for (const dimension of geometry.dimensions) {
                  picked[dimension.name] = toDisplay(row, dimension.index);
                }
                onMarkClick(picked);
              }}
            >
              {presmodel.columns.map((column, columnIndex) => {
                const raw = toDisplay(row, columnIndex);
                const isMeasure = column.role === "measure";
                return (
                  <td
                    key={column.name}
                    className={cn("px-3 py-2", isMeasure && "text-right font-medium")}
                    style={
                      colorMeasure && column.name === colorMeasure.name
                        ? { background: shade(numericOr(raw)) ?? undefined }
                        : undefined
                    }
                  >
                    {isMeasure ? formatMeasure(raw) : raw === null ? "–" : String(raw)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FallbackGrid({ presmodel }: { presmodel: PresModel }) {
  return (
    <div className="min-w-0 overflow-x-auto">
      <table className="type-ui w-full min-w-64 border-collapse">
        <thead>
          <tr className="border-b">
            {presmodel.columns.map((column) => (
              <th key={column.name} scope="col" className="type-label px-3 py-2 text-left text-ink-secondary">
                {column.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {presmodel.rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-b border-viz-grid last:border-b-0">
              {presmodel.columns.map((column, columnIndex) => (
                <td
                  key={column.name}
                  className={cn("px-3 py-2", column.role === "measure" && "text-right font-medium")}
                >
                  {column.role === "measure"
                    ? formatMeasure(toDisplay(row, columnIndex))
                    : toDisplay(row, columnIndex) === null
                      ? "–"
                      : String(toDisplay(row, columnIndex))}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
