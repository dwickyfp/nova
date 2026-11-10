/**
 * The chart builder for a tabular result (Workspaces query results and
 * notebook table outputs): pick a chart type, axes, aggregation and colours;
 * the data is the result already on screen, never a second query.
 */
import { memo, useEffect, useMemo, useState } from "react";
import { BarChart3, ChevronLeft, Hash, Plus, Type, X } from "lucide-react";
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
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { cn } from "@/lib/utils";
import { useTheme } from "@/context/theme-provider";
import { readToken } from "@/lib/read-token";
import type { QueryResponse } from "./types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

function readSeriesPalette() {
  return [
    readToken("--chart-4", "#4f7ee8"),
    readToken("--chart-2", "#14a89a"),
    readToken("--chart-3", "#e89b17"),
    readToken("--chart-1", "#f05a47"),
    readToken("--chart-6", "#a78bfa"),
    readToken("--chart-7", "#f472b6"),
    readToken("--chart-8", "#38bdf8"),
    readToken("--chart-9", "#34d399"),
  ];
}

function readTonePalette() {
  return [
    readToken("--chart-tone-1", "#5b8def"),
    readToken("--chart-tone-2", "#76a0ef"),
    readToken("--chart-tone-3", "#90b4f2"),
    readToken("--chart-tone-4", "#aac7f5"),
    readToken("--chart-tone-5", "#c4daf8"),
    readToken("--chart-tone-6", "#deedfb"),
  ];
}
const CHART_EMPTY_OPTION = "__none__";
const CHART_TYPE_OPTIONS = [
  { value: "bar", label: "Bar chart" },
  { value: "stacked-bar", label: "Stacked bar chart" },
  { value: "line", label: "Line chart" },
  { value: "area", label: "Area chart" },
  { value: "pie", label: "Pie chart" },
] as const;
const AGGREGATE_OPTIONS = [
  { value: "none", label: "None" },
  { value: "count", label: "Count" },
  { value: "min", label: "Min" },
  { value: "max", label: "Max" },
  { value: "sum", label: "Sum" },
  { value: "median", label: "Median" },
  { value: "average", label: "Average" },
] as const;
const SORT_OPTIONS = [
  { value: "none", label: "None" },
  { value: "x-asc", label: "X ascending" },
  { value: "x-desc", label: "X descending" },
  { value: "y-desc", label: "Y descending" },
  { value: "y-asc", label: "Y ascending" },
] as const;
const COLOR_OPTIONS = [
  { value: "palette", label: "Palette" },
  { value: "single", label: "Single tone" },
] as const;

type ColumnType = "number" | "date" | "text" | "boolean" | "null";
type ChartType = (typeof CHART_TYPE_OPTIONS)[number]["value"];
type AggregateType = (typeof AGGREGATE_OPTIONS)[number]["value"];
type SortMode = (typeof SORT_OPTIONS)[number]["value"];
type ColorMode = (typeof COLOR_OPTIONS)[number]["value"];
type ChartRecord = Record<string, string | number | boolean | null>;
type ChartSeries = {
  key: string;
  label: string;
  color: string;
};

function inferColumnType(
  rows: QueryResponse["rows"],
  colIndex: number,
): ColumnType {
  for (let i = 0; i < Math.min(rows.length, 20); i++) {
    const val = rows[i]?.[colIndex];
    if (val === null || val === undefined) continue;
    if (typeof val === "number") return "number";
    if (typeof val === "boolean") return "boolean";
    if (typeof val === "string") {
      if (/^\d{4}-\d{2}-\d{2}/.test(val)) return "date";
      if (/^[\d.,]+$/.test(val)) return "number";
      return "text";
    }
  }
  return "null";
}

function parseNumericValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const normalized = value.replace(/,/g, "").trim();
    if (!normalized) return null;
    const parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatChartLabel(value: unknown) {
  if (value === null || value === undefined || value === "") return "Unknown";
  return String(value);
}

function summarizeValues(values: number[], aggregate: AggregateType) {
  if (values.length === 0) return 0;

  switch (aggregate) {
    case "count":
      return values.length;
    case "min":
      return Math.min(...values);
    case "max":
      return Math.max(...values);
    case "average":
      return values.reduce((sum, value) => sum + value, 0) / values.length;
    case "median": {
      const sorted = [...values].sort((a, b) => a - b);
      const midpoint = Math.floor(sorted.length / 2);
      return sorted.length % 2 === 0
        ? (sorted[midpoint - 1] + sorted[midpoint]) / 2
        : sorted[midpoint];
    }
    case "sum":
    default:
      return values.reduce((sum, value) => sum + value, 0);
  }
}

function formatNumberCompact(value: number) {
  return new Intl.NumberFormat("en-US", {
    notation: Math.abs(value) >= 1000 ? "compact" : "standard",
    maximumFractionDigits: 2,
  }).format(value);
}

function getSeriesColors(colorMode: ColorMode, count: number) {
  const palette =
    colorMode === "single" ? readTonePalette() : readSeriesPalette();

  return Array.from(
    { length: count },
    (_, index) => palette[index % palette.length],
  );
}

function compareChartValues(
  left: string | number | boolean | null | undefined,
  right: string | number | boolean | null | undefined,
) {
  const leftNumber = parseNumericValue(left);
  const rightNumber = parseNumericValue(right);

  if (leftNumber != null && rightNumber != null)
    return leftNumber - rightNumber;

  return formatChartLabel(left).localeCompare(formatChartLabel(right));
}

function formatAxisTick(value: unknown, maxLength: number = 18) {
  const label = formatChartLabel(value);
  return label.length > maxLength ? `${label.slice(0, maxLength)}...` : label;
}

export const ChartVisualization = memo(function ChartVisualization({
  queryResult,
  builderInitiallyCollapsed = false,
}: {
  queryResult: QueryResponse | null;
  /** Start with the chart alone, the builder one click away (narrow hosts). */
  builderInitiallyCollapsed?: boolean;
}) {
  const { resolvedTheme } = useTheme();
  // Re-read whenever the theme flips: tokens resolve to different values. Declared
  // with the other hooks, above the empty-result early return, so the hook order
  // is identical whether or not the current result has rows.
  const chartTheme = useMemo(
    () => ({
      mutedForeground: readToken("--muted-foreground", "#5c6e87"),
      border: readToken("--border", "#e0e5ec"),
      popover: readToken("--popover", "#ffffff"),
      foreground: readToken("--foreground", "#1e293b"),
      background: readToken("--background", "#ffffff"),
    }),
    [resolvedTheme],
  );
  const [builderCollapsed, setBuilderCollapsed] = useState(builderInitiallyCollapsed);
  const [chartType, setChartType] = useState<ChartType>("bar");
  const [xCol, setXCol] = useState("");
  const [yCols, setYCols] = useState<string[]>([]);
  const [aggregate, setAggregate] = useState<AggregateType>("none");
  const [groupBy, setGroupBy] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("none");
  const [colorMode, setColorMode] = useState<ColorMode>("palette");
  const [showXAxisLabel, setShowXAxisLabel] = useState(true);
  const [showYAxisLabel, setShowYAxisLabel] = useState(true);
  const [xAxisLabel, setXAxisLabel] = useState("");
  const [yAxisLabel, setYAxisLabel] = useState("");

  const columnTypes = useMemo(() => {
    if (!queryResult?.columns.length) return [];
    return queryResult.columns.map((_, i) =>
      inferColumnType(queryResult.rows, i),
    );
  }, [queryResult]);

  const rawRecords = useMemo<ChartRecord[]>(() => {
    if (!queryResult) return [];

    return queryResult.rows.map((row) => {
      const record: ChartRecord = {};
      queryResult.columns.forEach((column, index) => {
        const rawValue = row[index];
        const columnType = columnTypes[index];

        if (columnType === "number") {
          record[column] = parseNumericValue(rawValue);
        } else {
          record[column] = rawValue;
        }
      });
      return record;
    });
  }, [columnTypes, queryResult]);

  const xCandidates = useMemo(
    () =>
      queryResult?.columns.filter(
        (_, index) => columnTypes[index] !== "null",
      ) ?? [],
    [columnTypes, queryResult],
  );

  const numericCandidates = useMemo(
    () =>
      queryResult?.columns.filter(
        (_, index) => columnTypes[index] === "number",
      ) ?? [],
    [columnTypes, queryResult],
  );

  // Auto-detect best chart config on new results
  useEffect(() => {
    if (!queryResult?.columns.length) return;

    const textColumns = queryResult.columns.filter(
      (_, index) => columnTypes[index] === "text",
    );
    const dateColumns = queryResult.columns.filter(
      (_, index) => columnTypes[index] === "date",
    );
    const numberColumns = queryResult.columns.filter(
      (_, index) => columnTypes[index] === "number",
    );

    const nextXColumn =
      dateColumns[0] ??
      textColumns[0] ??
      queryResult.columns.find((column) => !numberColumns.includes(column)) ??
      queryResult.columns[0] ??
      "";

    const nextYColumns =
      numberColumns.length > 0
        ? numberColumns.slice(0, dateColumns.length > 0 ? 2 : 3)
        : queryResult.columns.slice(1, 2);

    if (dateColumns.length >= 1 && numberColumns.length >= 1) {
      setChartType("line");
    } else if (numberColumns.length >= 1) {
      setChartType("bar");
    } else {
      setChartType("pie");
    }

    setXCol(nextXColumn);
    setYCols(nextYColumns);
    setAggregate("none");
    setGroupBy("");
    setSortMode("none");
    setColorMode("palette");
    setShowXAxisLabel(true);
    setShowYAxisLabel(true);
    setXAxisLabel(nextXColumn);
    setYAxisLabel(nextYColumns[0] ?? "");
  }, [columnTypes, queryResult]);

  useEffect(() => {
    if (chartType === "pie" && yCols.length > 1) {
      setYCols((current) => current.slice(0, 1));
    }
  }, [chartType, yCols]);

  useEffect(() => {
    if (groupBy && groupBy === xCol) {
      setGroupBy("");
    }
  }, [groupBy, xCol]);

  const effectiveYColumns = useMemo(() => {
    if (numericCandidates.length === 0) return [];

    const normalized = yCols.filter(
      (column, index) =>
        column &&
        numericCandidates.includes(column) &&
        yCols.indexOf(column) === index,
    );

    return chartType === "pie" ? normalized.slice(0, 1) : normalized;
  }, [chartType, numericCandidates, yCols]);

  const activeAggregate = useMemo<AggregateType>(() => {
    if (aggregate !== "none") return aggregate;
    if (chartType === "pie") return "sum";
    if (groupBy) return "sum";
    return "none";
  }, [aggregate, chartType, groupBy]);

  const chartModel = useMemo(() => {
    if (!queryResult || !xCol) {
      return {
        data: [] as Array<Record<string, string | number>>,
        series: [] as ChartSeries[],
        emptyReason:
          "Select an X-axis and at least one numeric metric to build the chart.",
      };
    }

    if (numericCandidates.length === 0) {
      return {
        data: [] as Array<Record<string, string | number>>,
        series: [] as ChartSeries[],
        emptyReason:
          "This result set has no numeric columns that can be visualized.",
      };
    }

    if (effectiveYColumns.length === 0 && activeAggregate !== "count") {
      return {
        data: [] as Array<Record<string, string | number>>,
        series: [] as ChartSeries[],
        emptyReason:
          "Pick at least one numeric Y-axis column to render the chart.",
      };
    }

    const sortData = (rows: Array<Record<string, string | number>>) => {
      if (sortMode === "none") return rows;

      const primarySeriesKey = Object.keys(rows[0] ?? {}).filter(
        (key) => key !== xCol,
      );

      return [...rows].sort((left, right) => {
        if (sortMode === "x-asc")
          return compareChartValues(left[xCol], right[xCol]);
        if (sortMode === "x-desc")
          return compareChartValues(right[xCol], left[xCol]);

        const leftTotal = primarySeriesKey.reduce(
          (sum, key) => sum + (parseNumericValue(left[key]) ?? 0),
          0,
        );
        const rightTotal = primarySeriesKey.reduce(
          (sum, key) => sum + (parseNumericValue(right[key]) ?? 0),
          0,
        );

        return sortMode === "y-asc"
          ? leftTotal - rightTotal
          : rightTotal - leftTotal;
      });
    };

    if (groupBy) {
      const primaryMetric = effectiveYColumns[0];
      if (!primaryMetric) {
        return {
          data: [] as Array<Record<string, string | number>>,
          series: [] as ChartSeries[],
          emptyReason: "Grouped charts need one numeric Y-axis metric.",
        };
      }

      const groupValues = Array.from(
        new Set(rawRecords.map((record) => formatChartLabel(record[groupBy]))),
      ).slice(0, 8);

      const bucketMap = new Map<string, Record<string, number[]>>();
      rawRecords.forEach((record) => {
        const xValue = formatChartLabel(record[xCol]);
        const groupValue = formatChartLabel(record[groupBy]);
        if (!groupValues.includes(groupValue)) return;

        const metricValue = parseNumericValue(record[primaryMetric]);
        if (metricValue == null && activeAggregate !== "count") return;

        const bucketKey = `${xValue}:::${groupValue}`;
        const existing = bucketMap.get(bucketKey) ?? {};
        existing[groupValue] = [
          ...(existing[groupValue] ?? []),
          metricValue ?? 0,
        ];
        bucketMap.set(bucketKey, existing);
      });

      const xValues = Array.from(
        new Set(rawRecords.map((record) => formatChartLabel(record[xCol]))),
      );

      const seriesColors = getSeriesColors(colorMode, groupValues.length);
      const data = sortData(
        xValues.map((xValue) => {
          const row: Record<string, string | number> = { [xCol]: xValue };

          groupValues.forEach((groupValue) => {
            const bucket = bucketMap.get(`${xValue}:::${groupValue}`);
            const values = bucket?.[groupValue] ?? [];
            row[groupValue] =
              activeAggregate === "count"
                ? values.length
                : summarizeValues(values, activeAggregate);
          });

          return row;
        }),
      );

      return {
        data,
        series: groupValues.map((groupValue, index) => ({
          key: groupValue,
          label: groupValue,
          color: seriesColors[index],
        })),
        emptyReason: "",
      };
    }

    if (activeAggregate !== "none" || chartType === "pie") {
      const groupedRows = new Map<
        string,
        {
          xLabel: string;
          metrics: Record<string, number[]>;
        }
      >();

      rawRecords.forEach((record) => {
        const xValue = formatChartLabel(record[xCol]);
        const bucket = groupedRows.get(xValue) ?? {
          xLabel: xValue,
          metrics: {},
        };

        if (activeAggregate === "count") {
          bucket.metrics.__count__ = [...(bucket.metrics.__count__ ?? []), 1];
        } else {
          effectiveYColumns.forEach((column) => {
            const numericValue = parseNumericValue(record[column]);
            if (numericValue == null) return;
            bucket.metrics[column] = [
              ...(bucket.metrics[column] ?? []),
              numericValue,
            ];
          });
        }

        groupedRows.set(xValue, bucket);
      });

      const seriesKeys =
        activeAggregate === "count" ? ["__count__"] : effectiveYColumns;
      const seriesColors = getSeriesColors(colorMode, seriesKeys.length);
      const data = sortData(
        Array.from(groupedRows.values()).map((bucket) => {
          const row: Record<string, string | number> = {
            [xCol]: bucket.xLabel,
          };

          seriesKeys.forEach((key) => {
            const values = bucket.metrics[key] ?? [];
            row[key] =
              activeAggregate === "count"
                ? values.length
                : summarizeValues(values, activeAggregate);
          });

          return row;
        }),
      );

      return {
        data,
        series: seriesKeys.map((key, index) => ({
          key,
          label: key === "__count__" ? "Count" : key,
          color: seriesColors[index],
        })),
        emptyReason: "",
      };
    }

    const seriesColors = getSeriesColors(colorMode, effectiveYColumns.length);
    const data = sortData(
      rawRecords.map((record) => {
        const row: Record<string, string | number> = {
          [xCol]: formatChartLabel(record[xCol]),
        };

        effectiveYColumns.forEach((column) => {
          row[column] = parseNumericValue(record[column]) ?? 0;
        });

        return row;
      }),
    );

    return {
      data,
      series: effectiveYColumns.map((column, index) => ({
        key: column,
        label: column,
        color: seriesColors[index],
      })),
      emptyReason: "",
    };
  }, [
    activeAggregate,
    colorMode,
    effectiveYColumns,
    groupBy,
    numericCandidates,
    queryResult,
    rawRecords,
    resolvedTheme,
    sortMode,
    xCol,
    chartType,
  ]);

  if (!queryResult?.columns.length || !queryResult.rows.length) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
        Run a query with results to visualize as a chart.
      </div>
    );
  }

  const availableMetricOptions = numericCandidates;
  const canAddMetric =
    chartType !== "pie" &&
    !groupBy &&
    availableMetricOptions.some(
      (column) => !effectiveYColumns.includes(column),
    ) &&
    effectiveYColumns.length < 4;
  const chartTitle =
    CHART_TYPE_OPTIONS.find((option) => option.value === chartType)?.label ??
    "Chart";
  const hasChartData =
    chartModel.data.length > 0 && chartModel.series.length > 0;
  const axisTickStyle = {
    fontSize: 11,
    fill: chartTheme.mutedForeground,
  };
  const axisLineStyle = {
    stroke: chartTheme.border,
  };
  const gridStroke = chartTheme.border;
  const tooltipContentStyle = {
    backgroundColor: chartTheme.popover,
    border: `1px solid ${chartTheme.border}`,
    borderRadius: 12,
    color: chartTheme.foreground,
    fontSize: 12,
    boxShadow: "var(--inset-shadow)",
  };
  const tooltipLabelStyle = {
    color: chartTheme.foreground,
    fontWeight: 600,
  };
  const tooltipItemStyle = {
    color: chartTheme.mutedForeground,
  };
  const hoverCursorStyle = {
    fill: chartTheme.border,
  };

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      <div
        className={cn(
          "relative border-b border-border bg-background transition-[width] duration-200 lg:border-b-0 lg:border-r",
          builderCollapsed ? "lg:w-14" : "lg:w-[320px]",
        )}
      >
        <button
          type="button"
          aria-label={
            builderCollapsed ? "Open chart builder" : "Collapse chart builder"
          }
          className="absolute right-2 top-2 z-10 inline-flex size-8 items-center justify-center rounded-md border border-border bg-background/90 text-muted-foreground shadow-sm transition-colors hover:bg-muted hover:text-foreground"
          onClick={() => setBuilderCollapsed((current) => !current)}
        >
          <ChevronLeft
            className={cn(
              "size-4 transition-transform",
              builderCollapsed && "rotate-180",
            )}
          />
        </button>

        {builderCollapsed ? (
          <div className="flex h-full min-h-0 flex-col items-center justify-start gap-3 px-2 py-12">
            <div className="flex size-9 items-center justify-center rounded-lg border border-border bg-muted/20">
              <BarChart3 className="size-4 text-muted-foreground" />
            </div>
            <div className="-rotate-180 text-xs font-medium text-muted-foreground [writing-mode:vertical-rl]">
              Chart
            </div>
          </div>
        ) : (
          <ScrollArea className="h-full">
            <div className="space-y-5 p-4">
              <div className="space-y-1 pr-10">
                <h3 className="text-sm font-semibold text-foreground">
                  Chart Builder
                </h3>
                <p className="text-xs text-muted-foreground">
                  Built directly from the current query result set. No
                  additional query is executed.
                </p>
              </div>

              <div className="space-y-4">
                <div className="space-y-2">
                  <label className="text-xs font-medium text-muted-foreground">
                    Chart type
                  </label>
                  <Select
                    value={chartType}
                    onValueChange={(value) => setChartType(value as ChartType)}
                  >
                    <SelectTrigger className="h-10 bg-muted/30">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CHART_TYPE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="border-t border-border pt-4">
                  <div className="space-y-2">
                    <label className="text-xs font-medium text-muted-foreground">
                      X-axis
                    </label>
                    <Select value={xCol} onValueChange={setXCol}>
                      <SelectTrigger className="h-10 bg-muted/30">
                        <div className="flex min-w-0 items-center gap-2">
                          <Type className="size-3.5 text-muted-foreground" />
                          <SelectValue />
                        </div>
                      </SelectTrigger>
                      <SelectContent>
                        {xCandidates.map((column) => (
                          <SelectItem key={column} value={column}>
                            {column}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="mt-4 space-y-2">
                    <label className="text-xs font-medium text-muted-foreground">
                      Sort
                    </label>
                    <Select
                      value={sortMode}
                      onValueChange={(value) => setSortMode(value as SortMode)}
                    >
                      <SelectTrigger className="h-10 bg-muted/30">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {SORT_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="border-t border-border pt-4">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium text-muted-foreground">
                      Y-axis
                    </label>
                    {canAddMetric ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => {
                          const nextMetric = availableMetricOptions.find(
                            (column) => !effectiveYColumns.includes(column),
                          );
                          if (!nextMetric) return;
                          setYCols((current) => [...current, nextMetric]);
                        }}
                      >
                        <Plus className="mr-1 size-3.5" />
                        Add column
                      </Button>
                    ) : null}
                  </div>

                  <div className="mt-2 space-y-2">
                    {effectiveYColumns.map((column, index) => (
                      <div
                        key={`${column}-${index}`}
                        className="flex items-center gap-2"
                      >
                        <Select
                          value={column}
                          onValueChange={(value) =>
                            setYCols((current) => {
                              const next = [...current];
                              next[index] = value;
                              return next;
                            })
                          }
                        >
                          <SelectTrigger className="h-10 flex-1 bg-muted/30">
                            <div className="flex min-w-0 items-center gap-2">
                              <Hash className="size-3.5 text-muted-foreground" />
                              <SelectValue />
                            </div>
                          </SelectTrigger>
                          <SelectContent>
                            {availableMetricOptions.map((metric) => (
                              <SelectItem key={metric} value={metric}>
                                {metric}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        {effectiveYColumns.length > 1 ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="size-9 shrink-0"
                            onClick={() =>
                              setYCols((current) =>
                                current.filter(
                                  (_, metricIndex) => metricIndex !== index,
                                ),
                              )
                            }
                          >
                            <X className="size-3.5" />
                          </Button>
                        ) : null}
                      </div>
                    ))}
                  </div>

                  {groupBy ? (
                    <p className="mt-2 text-xs text-muted-foreground">
                      Grouped charts use the first Y-axis metric as the measure
                      for each split series.
                    </p>
                  ) : null}
                </div>

                <div className="space-y-4 border-t border-border pt-4">
                  <div className="space-y-2">
                    <label className="text-xs font-medium text-muted-foreground">
                      Aggregate
                    </label>
                    <Select
                      value={aggregate}
                      onValueChange={(value) =>
                        setAggregate(value as AggregateType)
                      }
                    >
                      <SelectTrigger className="h-10 bg-muted/30">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {AGGREGATE_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <label className="text-xs font-medium text-muted-foreground">
                      Group by
                    </label>
                    <Select
                      value={groupBy || CHART_EMPTY_OPTION}
                      onValueChange={(value) =>
                        setGroupBy(value === CHART_EMPTY_OPTION ? "" : value)
                      }
                    >
                      <SelectTrigger className="h-10 bg-muted/30">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={CHART_EMPTY_OPTION}>None</SelectItem>
                        {xCandidates
                          .filter((column) => column !== xCol)
                          .map((column) => (
                            <SelectItem key={column} value={column}>
                              {column}
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <label className="text-xs font-medium text-muted-foreground">
                      Color
                    </label>
                    <Select
                      value={colorMode}
                      onValueChange={(value) =>
                        setColorMode(value as ColorMode)
                      }
                    >
                      <SelectTrigger className="h-10 bg-muted/30">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {COLOR_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="space-y-4 border-t border-border pt-4">
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-medium text-muted-foreground">
                        X-axis label
                      </label>
                      <Switch
                        checked={showXAxisLabel}
                        onCheckedChange={setShowXAxisLabel}
                      />
                    </div>
                    <Input
                      value={xAxisLabel}
                      onChange={(event) => setXAxisLabel(event.target.value)}
                      placeholder={xCol}
                      disabled={!showXAxisLabel}
                      className="h-10"
                    />
                  </div>

                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-medium text-muted-foreground">
                        Y-axis label
                      </label>
                      <Switch
                        checked={showYAxisLabel}
                        onCheckedChange={setShowYAxisLabel}
                      />
                    </div>
                    <Input
                      value={yAxisLabel}
                      onChange={(event) => setYAxisLabel(event.target.value)}
                      placeholder={effectiveYColumns[0] ?? "Value"}
                      disabled={!showYAxisLabel}
                      className="h-10"
                    />
                  </div>
                </div>
              </div>
            </div>
          </ScrollArea>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col bg-background">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <BarChart3 className="size-4 text-primary" />
              <h3 className="text-sm font-semibold text-foreground">
                {chartTitle}
              </h3>
            </div>
            <p className="text-xs text-muted-foreground">
              {queryResult.row_count.toLocaleString()} rows in result •{" "}
              {chartModel.data.length.toLocaleString()} plotted groups
            </p>
          </div>

          {chartModel.series.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2">
              {chartModel.series.map((series) => (
                <div
                  key={series.key}
                  className="inline-flex items-center gap-2 text-xs text-muted-foreground"
                >
                  <span
                    className="size-2 rounded-full"
                    style={{ backgroundColor: series.color }}
                  />
                  <span className="max-w-[140px] truncate">{series.label}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>

        <div className="min-h-0 flex-1 p-5">
          <div className="h-full rounded-xl border border-border/80 bg-muted/10 p-4">
            {hasChartData ? (
              <ResponsiveContainer width="100%" height="100%">
                {chartType === "line" ? (
                  <LineChart data={chartModel.data}>
                    <CartesianGrid
                      stroke={gridStroke}
                      opacity={0.7}
                      vertical={false}
                    />
                    <XAxis
                      dataKey={xCol}
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) => formatAxisTick(value)}
                      minTickGap={18}
                      label={
                        showXAxisLabel && xAxisLabel
                          ? {
                              value: xAxisLabel,
                              position: "insideBottom",
                              offset: -6,
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <YAxis
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) =>
                        formatNumberCompact(Number(value))
                      }
                      width={68}
                      label={
                        showYAxisLabel && yAxisLabel
                          ? {
                              value: yAxisLabel,
                              angle: -90,
                              position: "insideLeft",
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <Tooltip
                      formatter={(value) =>
                        formatNumberCompact(Number(value ?? 0))
                      }
                      contentStyle={tooltipContentStyle}
                      labelStyle={tooltipLabelStyle}
                      itemStyle={tooltipItemStyle}
                      cursor={{
                        stroke: chartTheme.mutedForeground,
                        strokeWidth: 1,
                      }}
                    />
                    {chartModel.series.map((series) => (
                      <Line
                        key={series.key}
                        type="monotone"
                        dataKey={series.key}
                        stroke={series.color}
                        strokeWidth={2.5}
                        dot={{ r: 0 }}
                        activeDot={{
                          r: 4,
                          fill: series.color,
                          stroke: chartTheme.background,
                          strokeWidth: 2,
                        }}
                      />
                    ))}
                  </LineChart>
                ) : chartType === "area" ? (
                  <AreaChart data={chartModel.data}>
                    <defs>
                      {chartModel.series.map((series) => (
                        <linearGradient
                          key={series.key}
                          id={`area-gradient-${series.key}`}
                          x1="0"
                          y1="0"
                          x2="0"
                          y2="1"
                        >
                          <stop
                            offset="0%"
                            stopColor={series.color}
                            stopOpacity={0.55}
                          />
                          <stop
                            offset="100%"
                            stopColor={series.color}
                            stopOpacity={0.04}
                          />
                        </linearGradient>
                      ))}
                    </defs>
                    <CartesianGrid
                      stroke={gridStroke}
                      opacity={0.7}
                      vertical={false}
                    />
                    <XAxis
                      dataKey={xCol}
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) => formatAxisTick(value)}
                      minTickGap={18}
                      label={
                        showXAxisLabel && xAxisLabel
                          ? {
                              value: xAxisLabel,
                              position: "insideBottom",
                              offset: -6,
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <YAxis
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) =>
                        formatNumberCompact(Number(value))
                      }
                      width={68}
                      label={
                        showYAxisLabel && yAxisLabel
                          ? {
                              value: yAxisLabel,
                              angle: -90,
                              position: "insideLeft",
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <Tooltip
                      formatter={(value) =>
                        formatNumberCompact(Number(value ?? 0))
                      }
                      contentStyle={tooltipContentStyle}
                      labelStyle={tooltipLabelStyle}
                      itemStyle={tooltipItemStyle}
                      cursor={{
                        stroke: chartTheme.mutedForeground,
                        strokeWidth: 1,
                      }}
                    />
                    {chartModel.series.map((series) => (
                      <Area
                        key={series.key}
                        type="monotone"
                        dataKey={series.key}
                        stroke={series.color}
                        strokeWidth={2.5}
                        fill={`url(#area-gradient-${series.key})`}
                        fillOpacity={1}
                      />
                    ))}
                  </AreaChart>
                ) : chartType === "pie" ? (
                  <PieChart>
                    <Tooltip
                      formatter={(value) =>
                        formatNumberCompact(Number(value ?? 0))
                      }
                      contentStyle={tooltipContentStyle}
                      labelStyle={tooltipLabelStyle}
                      itemStyle={tooltipItemStyle}
                    />
                    <Pie
                      data={chartModel.data}
                      dataKey={chartModel.series[0]?.key}
                      nameKey={xCol}
                      cx="50%"
                      cy="50%"
                      innerRadius="48%"
                      outerRadius="74%"
                      paddingAngle={2}
                      stroke={chartTheme.background}
                      strokeWidth={2}
                    >
                      {chartModel.data.map((_, index) => (
                        <Cell
                          key={`pie-cell-${index}`}
                          fill={
                            chartModel.series.length > 1
                              ? chartModel.series[
                                  index % chartModel.series.length
                                ]?.color
                              : getSeriesColors(
                                  colorMode,
                                  chartModel.data.length,
                                )[index]
                          }
                        />
                      ))}
                    </Pie>
                  </PieChart>
                ) : (
                  <BarChart data={chartModel.data} barGap={10}>
                    <defs>
                      {chartModel.series.map((series) => (
                        <linearGradient
                          key={series.key}
                          id={`bar-gradient-${series.key}`}
                          x1="0"
                          y1="0"
                          x2="0"
                          y2="1"
                        >
                          <stop
                            offset="0%"
                            stopColor={series.color}
                            stopOpacity={0.95}
                          />
                          <stop
                            offset="100%"
                            stopColor={series.color}
                            stopOpacity={0.75}
                          />
                        </linearGradient>
                      ))}
                    </defs>
                    <CartesianGrid
                      stroke={gridStroke}
                      opacity={0.7}
                      vertical={false}
                    />
                    <XAxis
                      dataKey={xCol}
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) => formatAxisTick(value)}
                      minTickGap={16}
                      angle={chartModel.data.length > 8 ? -90 : 0}
                      textAnchor={chartModel.data.length > 8 ? "end" : "middle"}
                      height={chartModel.data.length > 8 ? 92 : 46}
                      label={
                        showXAxisLabel && xAxisLabel
                          ? {
                              value: xAxisLabel,
                              position: "insideBottom",
                              offset: chartModel.data.length > 8 ? -2 : -6,
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <YAxis
                      tick={axisTickStyle}
                      axisLine={axisLineStyle}
                      tickLine={axisLineStyle}
                      tickFormatter={(value) =>
                        formatNumberCompact(Number(value))
                      }
                      width={68}
                      label={
                        showYAxisLabel && yAxisLabel
                          ? {
                              value: yAxisLabel,
                              angle: -90,
                              position: "insideLeft",
                              style: {
                                fill: axisTickStyle.fill,
                                fontSize: 12,
                              },
                            }
                          : undefined
                      }
                    />
                    <Tooltip
                      formatter={(value) =>
                        formatNumberCompact(Number(value ?? 0))
                      }
                      contentStyle={tooltipContentStyle}
                      labelStyle={tooltipLabelStyle}
                      itemStyle={tooltipItemStyle}
                      cursor={hoverCursorStyle}
                    />
                    {chartModel.series.map((series) => (
                      <Bar
                        key={series.key}
                        dataKey={series.key}
                        fill={`url(#bar-gradient-${series.key})`}
                        radius={[8, 8, 0, 0]}
                        stackId={
                          chartType === "stacked-bar"
                            ? "chart-stack"
                            : undefined
                        }
                        maxBarSize={chartType === "stacked-bar" ? 44 : 56}
                      />
                    ))}
                  </BarChart>
                )}
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                {chartModel.emptyReason}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
});
