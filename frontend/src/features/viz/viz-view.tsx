import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
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
  ZAxis,
} from "recharts";
import { cn } from "@/lib/utils";
import type { PresModel } from "./types";

/**
 * VizView draws a presentation model the viz core computed: marks
 * (bar, line, area, circle, pie, text) with their encodings, on Nova tokens.
 * It never fetches and never parses SQL; it renders what the core decided.
 */

const seriesColor = (index: number) => `var(--chart-${(index % 8) + 1})`;

/** Abbreviated number format: 1.2K, 3.4M, 5B. */
export function formatMeasure(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "–";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  const abs = Math.abs(numeric);
  if (abs >= 1e12) return `${trim(numeric / 1e12)}T`;
  if (abs >= 1e9) return `${trim(numeric / 1e9)}B`;
  if (abs >= 1e6) return `${trim(numeric / 1e6)}M`;
  if (abs >= 1e3) return `${trim(numeric / 1e3)}K`;
  return trim(numeric);
}

function trim(value: number): string {
  const abs = Math.abs(value);
  // Ratios and shares need two decimals to stay distinct (0.15 vs 0.1).
  const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  const rounded = value.toFixed(digits);
  return digits ? rounded.replace(/\.?0+$/, "") : rounded;
}

type CellValue = string | number | null;

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
}: {
  presmodel: PresModel;
  compact?: boolean;
  /** Fill the parent with marks only (no axes): library thumbnails. */
  thumbnail?: boolean;
  className?: string;
  onMarkClick?: (dimensionValues: Record<string, CellValue>) => void;
  selectedMark?: Record<string, CellValue> | null;
}) {
  const geometry = useMemo(() => buildGeometry(presmodel), [presmodel]);
  if (!presmodel.rows.length) {
    return (
      <p className={cn("py-8 text-center text-sm text-muted-foreground", className)}>
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
      className={cn("min-w-0", thumbnail && "flex h-full flex-col gap-1", className)}
      aria-label={presmodel.title || "Chart"}
    >
      {panes.map((pane) => (
        <div
          key={pane.series.map((series) => series.name).join("|")}
          className={cn("min-w-0", thumbnail && "min-h-0 flex-1")}
        >
          {panes.length > 1 && !thumbnail ? (
            <p className="px-1 text-xs font-medium text-muted-foreground">{measureCaption(pane.series[0].name)}</p>
          ) : null}
          <Marks
            presmodel={presmodel}
            geometry={pane}
            compact={compact || panes.length > 2}
            thumbnail={thumbnail}
            onMarkClick={onMarkClick}
            selectedMark={selectedMark ?? null}
          />
        </div>
      ))}
    </section>
  );
}

/** "sum(sales)" reads as "SUM(sales)", as on the shelf. */
function measureCaption(label: string): string {
  const match = /^([a-z_]+)\((.*)\)$/.exec(label);
  if (!match) return label;
  const prefix: Record<string, string> = { distinct_count: "CNTD", count: "CNT", avg: "AVG" };
  return `${prefix[match[1]] ?? match[1].toUpperCase()}(${match[2]})`;
}

const PANED_MARKS: ReadonlySet<string> = new Set(["bar", "line", "area", "tick", "square"]);

type Geometry = {
  xField: string;
  xType: "category" | "number";
  series: { name: string; index: number }[];
  colorField: string | null;
  colorKind: "categorical" | "quantitative" | null;
  colorDomain: (string | number)[];
  sizeField: string | null;
  chartData: Record<string, unknown>[];
  dimensions: { name: string; index: number }[];
  measures: { name: string; index: number }[];
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
  const colorIsDimension = dimensions.some(
    (dimension) => dimension.name === colorField,
  );
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
  const xType =
    axisDimension || presmodel.axes.x?.discrete !== false ? "category" : "number";

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
    sizeField: null,
    chartData,
    dimensions,
    measures,
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

function Marks({
  presmodel,
  geometry,
  compact,
  thumbnail = false,
  onMarkClick,
  selectedMark,
}: {
  presmodel: PresModel;
  geometry: Geometry;
  compact: boolean;
  thumbnail?: boolean;
  onMarkClick?: (values: Record<string, CellValue>) => void;
  selectedMark: Record<string, CellValue> | null;
}) {
  const mark = presmodel.mark;
  const height = thumbnail ? "h-full" : compact ? "h-40" : "h-64";
  const axisProps = {
    hide: thumbnail,
    stroke: "var(--muted-foreground)",
    tick: { fontSize: 12, fill: "var(--muted-foreground)" },
    tickFormatter: (value: string | number) =>
      typeof value === "number" ? formatMeasure(value) : String(value),
  };
  const tooltip = (
    <Tooltip
      itemStyle={{ color: "var(--foreground)" }}
      contentStyle={{
        background: "var(--popover)",
        borderColor: "var(--border)",
        color: "var(--foreground)",
      }}
      formatter={(value) => (typeof value === "number" ? formatMeasure(value) : value)}
    />
  );

  if (mark === "text") {
    return (
      <TextTable
        presmodel={presmodel}
        geometry={geometry}
        onMarkClick={onMarkClick}
        selectedMark={selectedMark}
      />
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
              innerRadius="45%"
              outerRadius="85%"
              stroke="var(--card)"
              strokeWidth={2}
              isAnimationActive={false}
            >
              {geometry.chartData.map((_, index) => (
                <Cell key={index} fill={colorForIndex(geometry, index)} />
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
      fill: colorForIndex(geometry, index),
    }));
    return (
      <div className={cn(height, "min-w-0")}>
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
            {thumbnail ? null : <CartesianGrid stroke="var(--border)" />}
            <XAxis
              dataKey="x"
              type="number"
              name={xAxisMeasure.name}
              {...axisProps}
            />
            <YAxis
              dataKey="y"
              type="number"
              name={yAxisMeasure.name}
              {...axisProps}
            />
            <ZAxis range={[60, 60]} />
            {tooltip}
            <Scatter
              data={scatterData}
              fill="var(--chart-1)"
              isAnimationActive={false}
              shape={mark === "square" ? "square" : "circle"}
            />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    );
  }

  const shared = { data: geometry.chartData, margin: { top: 8, right: 16, bottom: 8, left: 8 } };
  const xAxis = (
    <XAxis dataKey={geometry.xField} type={geometry.xType === "number" ? "number" : "category"} {...axisProps} />
  );
  const yAxis = <YAxis {...axisProps} width={56} />;

  if (mark === "line") {
    return (
      <div className={cn(height, "min-w-0")}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart {...shared}>
            {thumbnail ? null : <CartesianGrid stroke="var(--border)" />}
            {xAxis}
            {yAxis}
            {tooltip}
            {geometry.series.map((series, index) => (
              <Line
                key={series.name}
                dataKey={series.name}
                name={series.name}
                stroke={colorForIndex(geometry, index)}
                strokeWidth={2.5}
                dot={thumbnail ? false : { r: 3, fill: colorForIndex(geometry, index), strokeWidth: 0 }}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    );
  }

  if (mark === "area") {
    return (
      <div className={cn(height, "min-w-0")}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart {...shared}>
            {thumbnail ? null : <CartesianGrid stroke="var(--border)" />}
            {xAxis}
            {yAxis}
            {tooltip}
            {geometry.series.map((series, index) => (
              <Area
                key={series.name}
                dataKey={series.name}
                name={series.name}
                stroke={colorForIndex(geometry, index)}
                fill={colorForIndex(geometry, index)}
                fillOpacity={0.25}
                strokeWidth={2}
                isAnimationActive={false}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // bar and tick share the categorical frame
  return (
    <div className={cn(height, "min-w-0")}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart {...shared}>
          {thumbnail ? null : (
            <CartesianGrid stroke="var(--border)" vertical={mark !== "tick"} horizontal={mark === "tick"} />
          )}
          {xAxis}
          {yAxis}
          {tooltip}
          {geometry.series.map((series, index) => (
            <Bar
              key={series.name}
              dataKey={series.name}
              name={series.name}
              fill={colorForIndex(geometry, index)}
              radius={mark === "bar" ? [3, 3, 0, 0] : undefined}
              barSize={mark === "tick" ? 4 : undefined}
              isAnimationActive={false}
              cursor={onMarkClick ? "pointer" : undefined}
              onClick={(data: { payload?: Record<string, unknown> }) => {
                if (onMarkClick && data?.payload) {
                  const values: Record<string, CellValue> = {};
                  for (const dimension of geometry.dimensions) {
                    values[dimension.name] = (data.payload[dimension.name] as CellValue) ?? null;
                  }
                  onMarkClick(values);
                }
              }}
            >
              {geometry.colorKind === "categorical"
                ? geometry.chartData.map((_, cellIndex) => (
                    <Cell key={cellIndex} fill={colorForIndex(geometry, cellIndex)} />
                  ))
                : null}
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
  const colorMeasure = geometry.series.find(
    (series) => series.name === geometry.colorField,
  );
  const values = colorMeasure
    ? presmodel.rows
        .map((row) => numericOr(toDisplay(row, colorMeasure.index)))
        .filter((value): value is number => value !== null)
    : [];
  const extent = values.length
    ? { min: Math.min(...values), max: Math.max(...values) }
    : null;
  const shade = (value: number | null): string | null => {
    if (!extent || value === null || extent.max === extent.min) return null;
    const ratio = (value - extent.min) / (extent.max - extent.min);
    return `color-mix(in oklab, var(--chart-1) ${Math.round(18 + ratio * 62)}%, transparent)`;
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
      <table className="w-full min-w-64 border-collapse text-sm">
        <thead>
          <tr className="border-b">
            {presmodel.columns.map((column) => (
              <th
                key={column.name}
                scope="col"
                className="px-3 py-2 text-left text-xs font-medium text-muted-foreground"
              >
                {column.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {presmodel.rows.map((row, rowIndex) => (
            <tr
              key={rowIndex}
              className={cn(
                "border-b last:border-b-0",
                onMarkClick && "cursor-pointer hover:bg-muted/50",
                isSelected(row) && "bg-muted",
              )}
              onClick={() => {
                if (!onMarkClick) return;
                const values: Record<string, CellValue> = {};
                for (const dimension of geometry.dimensions) {
                  values[dimension.name] = toDisplay(row, dimension.index);
                }
                onMarkClick(values);
              }}
            >
              {presmodel.columns.map((column, columnIndex) => {
                const raw = toDisplay(row, columnIndex);
                const isMeasure = column.role === "measure";
                return (
                  <td
                    key={column.name}
                    className={cn(
                      "px-3 py-2 tabular-nums",
                      isMeasure && "text-right font-medium",
                    )}
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
      <table className="w-full min-w-64 border-collapse text-sm">
        <thead>
          <tr className="border-b">
            {presmodel.columns.map((column) => (
              <th
                key={column.name}
                scope="col"
                className="px-3 py-2 text-left text-xs font-medium text-muted-foreground"
              >
                {column.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {presmodel.rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-b last:border-b-0">
              {presmodel.columns.map((column, columnIndex) => (
                <td
                  key={column.name}
                  className={cn(
                    "px-3 py-2 tabular-nums",
                    column.role === "measure" && "text-right font-medium",
                  )}
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
