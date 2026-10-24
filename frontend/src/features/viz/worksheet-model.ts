import type {
  Aggregation,
  CalculatedField,
  DataPaneField,
  DatePart,
  FieldRef,
  FieldType,
  Filter,
  MarksCard,
  MarkType,
  WorksheetSpec,
} from "./types";

/**
 * Pure worksheet operations for the authoring surface: building pills from
 * data-pane fields, placing and moving them between shelves, changing their
 * aggregation or date level, and an undo/redo history. Every function
 * returns a new spec; nothing here talks to the server.
 */

export type Shelf = "columns" | "rows";
export type Encoding = "color" | "size" | "text" | "detail" | "angle";
export type DropTarget = Shelf | Encoding | "filters";

/** The pill a record count produces; the compiler reads it as COUNT(*). */
export const RECORDS_FIELD = "*";

export const AGGREGATION_LABELS: Record<Exclude<Aggregation, "agg">, string> = {
  sum: "Sum",
  avg: "Average",
  median: "Median",
  count: "Count",
  distinct_count: "Count (distinct)",
  min: "Minimum",
  max: "Maximum",
  stdev: "Std. deviation",
  stdevp: "Std. deviation (population)",
  var: "Variance",
  varp: "Variance (population)",
  attr: "Attribute",
};

const AGGREGATION_PREFIX: Record<Aggregation, string> = {
  sum: "SUM",
  avg: "AVG",
  median: "MEDIAN",
  count: "CNT",
  distinct_count: "CNTD",
  min: "MIN",
  max: "MAX",
  stdev: "STDEV",
  stdevp: "STDEVP",
  var: "VAR",
  varp: "VARP",
  attr: "ATTR",
  agg: "AGG",
};

/** Aggregations that need numeric input. */
export const NUMERIC_AGGREGATIONS: ReadonlySet<Aggregation> = new Set([
  "sum",
  "avg",
  "median",
  "stdev",
  "stdevp",
  "var",
  "varp",
]);

export const DATE_PARTS: { value: DatePart; label: string }[] = [
  { value: "year", label: "Year" },
  { value: "quarter", label: "Quarter" },
  { value: "month", label: "Month" },
  { value: "week", label: "Week" },
  { value: "day", label: "Day" },
];

export const QUICK_TABLE_CALCS: { kind: string; label: string }[] = [
  { kind: "running_total", label: "Running total" },
  { kind: "percent_of_total", label: "Percent of total" },
  { kind: "difference", label: "Difference" },
  { kind: "percent_difference", label: "Percent difference" },
  { kind: "rank", label: "Rank" },
];

export const ENCODING_LABELS: Record<Encoding, string> = {
  color: "Color",
  size: "Size",
  text: "Label",
  detail: "Detail",
  angle: "Angle",
};

export function isTemporal(field: Pick<DataPaneField, "data_type">): boolean {
  return field.data_type === "date" || field.data_type === "datetime";
}

function fieldTypeOf(field: DataPaneField): FieldType {
  if (field.data_type === "number") return "quantitative";
  if (isTemporal(field)) return "temporal";
  return "nominal";
}

/** The pill a data-pane field becomes when dropped on a shelf. */
export function pillFromField(field: DataPaneField): FieldRef {
  if (field.level === "aggregate" || field.level === "table") {
    return { field: field.name, field_type: "quantitative", aggregation: "agg", discrete: false };
  }
  if (field.role === "measure") {
    return {
      field: field.name,
      field_type: "quantitative",
      aggregation: field.data_type === "number" ? "sum" : "count",
      discrete: false,
    };
  }
  if (isTemporal(field)) {
    return { field: field.name, field_type: "ordinal", discrete: true, date_part: "year" };
  }
  return { field: field.name, field_type: fieldTypeOf(field), discrete: true };
}

export function recordsPill(): FieldRef {
  return { field: RECORDS_FIELD, field_type: "quantitative", aggregation: "count", discrete: false };
}

export function isMeasure(pill: FieldRef): boolean {
  return pill.aggregation !== null && pill.aggregation !== undefined;
}

/** Continuous pills (measures, truncated dates, continuous dimensions) draw axes. */
export function isContinuous(pill: FieldRef): boolean {
  return isMeasure(pill) || !pill.discrete;
}

/** The pill's caption, as the shelf shows it: SUM(Sales), YEAR(Order Date). */
export function pillCaption(pill: FieldRef): string {
  const name = pill.field === RECORDS_FIELD ? "Records" : pill.field;
  if (pill.aggregation) return `${AGGREGATION_PREFIX[pill.aggregation]}(${name})`;
  if (pill.date_part) return `${pill.date_part.toUpperCase()}(${name})`;
  return name;
}

/** The result column a pill produces, matching the compiler's label. */
export function pillLabel(pill: FieldRef): string {
  if (pill.aggregation === "agg") return pill.field;
  if (pill.aggregation) return `${pill.aggregation}(${pill.field})`;
  if (pill.date_part) return `${pill.date_part}${pill.discrete ? "" : "_trunc"}(${pill.field})`;
  return pill.field;
}

export function emptyMarks(type: MarkType = "bar"): MarksCard {
  return { type, color: null, size: null, text: null, detail: null, angle: null };
}

/** True when the sheet has something to compute. */
export function hasView(spec: WorksheetSpec): boolean {
  return spec.columns.length > 0 || spec.rows.length > 0;
}

export function viewDimensions(spec: WorksheetSpec): FieldRef[] {
  const seen = new Map<string, FieldRef>();
  const marks = spec.marks;
  for (const pill of [
    ...spec.columns,
    ...spec.rows,
    marks.color,
    marks.size,
    marks.text,
    marks.detail,
    marks.angle,
  ]) {
    if (pill && !isMeasure(pill) && !seen.has(pillLabel(pill))) seen.set(pillLabel(pill), pill);
  }
  return [...seen.values()];
}

/** Place a pill on a shelf (at ``index``) or an encoding slot. */
export function placePill(
  spec: WorksheetSpec,
  target: Shelf | Encoding,
  pill: FieldRef,
  index?: number,
): WorksheetSpec {
  if (target === "columns" || target === "rows") {
    const shelf = spec[target].filter((item) => pillLabel(item) !== pillLabel(pill));
    const at = index === undefined ? shelf.length : Math.max(0, Math.min(index, shelf.length));
    shelf.splice(at, 0, pill);
    return { ...spec, [target]: shelf.slice(0, 8) };
  }
  return { ...spec, marks: { ...spec.marks, [target]: pill } };
}

export type PillLocation = { target: Shelf | Encoding; index: number };

export function pillAt(spec: WorksheetSpec, location: PillLocation): FieldRef | null {
  if (location.target === "columns" || location.target === "rows") {
    return spec[location.target][location.index] ?? null;
  }
  return spec.marks[location.target] ?? null;
}

export function removePill(spec: WorksheetSpec, location: PillLocation): WorksheetSpec {
  const pill = pillAt(spec, location);
  let next: WorksheetSpec;
  if (location.target === "columns" || location.target === "rows") {
    const shelf = spec[location.target].filter((_, index) => index !== location.index);
    next = { ...spec, [location.target]: shelf };
  } else {
    next = { ...spec, marks: { ...spec.marks, [location.target]: null } };
  }
  if (!pill) return next;
  // Sorts and quick calcs that named the removed pill go with it.
  const label = pillLabel(pill);
  const stillUsed = allPills(next).some((item) => pillLabel(item) === label);
  if (stillUsed) return next;
  return {
    ...next,
    sort: (next.sort ?? []).filter((item) => item.field !== label && item.field !== pill.field),
    table_calcs: (next.table_calcs ?? []).filter((item) => item.field !== label),
  };
}

/** Move a pill to another shelf or slot (drag between shelves). */
export function movePill(
  spec: WorksheetSpec,
  from: PillLocation,
  target: Shelf | Encoding,
  index?: number,
): WorksheetSpec {
  const pill = pillAt(spec, from);
  if (!pill) return spec;
  if (from.target === target && (target === "columns" || target === "rows")) {
    const shelf = [...spec[target]];
    shelf.splice(from.index, 1);
    const at = index === undefined ? shelf.length : Math.min(index > from.index ? index - 1 : index, shelf.length);
    shelf.splice(at, 0, pill);
    return { ...spec, [target]: shelf };
  }
  const without =
    from.target === "columns" || from.target === "rows"
      ? { ...spec, [from.target]: spec[from.target].filter((_, i) => i !== from.index) }
      : { ...spec, marks: { ...spec.marks, [from.target]: null } };
  return placePill(without, target, pill, index);
}

export function updatePill(
  spec: WorksheetSpec,
  location: PillLocation,
  change: (pill: FieldRef) => FieldRef,
): WorksheetSpec {
  const pill = pillAt(spec, location);
  if (!pill) return spec;
  const next = change(pill);
  const before = pillLabel(pill);
  const after = pillLabel(next);
  const renamed = (field: string) => (field === before ? after : field);
  let updated: WorksheetSpec;
  if (location.target === "columns" || location.target === "rows") {
    const shelf = spec[location.target].map((item, index) => (index === location.index ? next : item));
    updated = { ...spec, [location.target]: shelf };
  } else {
    updated = { ...spec, marks: { ...spec.marks, [location.target]: next } };
  }
  return {
    ...updated,
    sort: (updated.sort ?? []).map((item) => ({ ...item, field: renamed(item.field) })),
    table_calcs: (updated.table_calcs ?? []).map((item) => ({ ...item, field: renamed(item.field) })),
  };
}

export function setAggregation(pill: FieldRef, aggregation: Aggregation): FieldRef {
  return { ...pill, aggregation, discrete: false, date_part: null, field_type: "quantitative" };
}

/** Turn a measure pill into a dimension pill. */
export function asDimension(pill: FieldRef, field?: DataPaneField): FieldRef {
  const temporal = field ? isTemporal(field) : pill.field_type === "temporal";
  return {
    field: pill.field,
    field_type: temporal ? "ordinal" : field ? fieldTypeOf(field) : "nominal",
    aggregation: null,
    discrete: true,
    date_part: temporal ? "year" : null,
  };
}

export function setDatePart(pill: FieldRef, part: DatePart | null, discrete: boolean): FieldRef {
  return {
    ...pill,
    aggregation: null,
    date_part: part,
    discrete,
    field_type: part ? (discrete ? "ordinal" : "temporal") : "temporal",
  };
}

export function toggleDiscrete(pill: FieldRef): FieldRef {
  return { ...pill, discrete: !pill.discrete };
}

export function swapShelves(spec: WorksheetSpec): WorksheetSpec {
  return { ...spec, columns: spec.rows, rows: spec.columns };
}

export function clearSheet(spec: WorksheetSpec): WorksheetSpec {
  return {
    ...spec,
    columns: [],
    rows: [],
    marks: emptyMarks(spec.marks.type),
    filters: [],
    sort: [],
    table_calcs: [],
    limit: null,
  };
}

export function setSort(
  spec: WorksheetSpec,
  pill: FieldRef,
  direction: "asc" | "desc" | null,
): WorksheetSpec {
  const field = isMeasure(pill) ? pillLabel(pill) : pill.field;
  const rest = (spec.sort ?? []).filter((item) => item.field !== field && item.field !== pillLabel(pill));
  return { ...spec, sort: direction ? [{ field, direction }, ...rest].slice(0, 4) : rest };
}

export function sortDirection(spec: WorksheetSpec, pill: FieldRef): "asc" | "desc" | null {
  const field = isMeasure(pill) ? pillLabel(pill) : pill.field;
  return (spec.sort ?? []).find((item) => item.field === field || item.field === pillLabel(pill))?.direction ?? null;
}

export function setQuickTableCalc(spec: WorksheetSpec, pill: FieldRef, kind: string | null): WorksheetSpec {
  const label = pillLabel(pill);
  const rest = (spec.table_calcs ?? []).filter((item) => item.field !== label);
  return { ...spec, table_calcs: kind ? [...rest, { kind, field: label }].slice(0, 8) : rest };
}

export function quickTableCalc(spec: WorksheetSpec, pill: FieldRef): string | null {
  const label = pillLabel(pill);
  return (spec.table_calcs ?? []).find((item) => item.field === label)?.kind ?? null;
}

export function upsertFilter(spec: WorksheetSpec, filter: Filter, index?: number): WorksheetSpec {
  const filters = [...(spec.filters ?? [])];
  if (index !== undefined && index < filters.length) filters[index] = filter;
  else filters.push(filter);
  return { ...spec, filters: filters.slice(0, 32) };
}

export function removeFilter(spec: WorksheetSpec, index: number): WorksheetSpec {
  return { ...spec, filters: (spec.filters ?? []).filter((_, i) => i !== index) };
}

export function filterCaption(filter: Filter): string {
  if (filter.kind === "quantitative" && filter.aggregation) {
    return `${AGGREGATION_PREFIX[filter.aggregation]}(${filter.field})`;
  }
  return filter.field;
}

export function filterSummary(filter: Filter): string {
  if (filter.kind === "categorical") {
    if (!filter.values.length) return filter.exclude ? "All values" : "No values";
    const shown = filter.values.slice(0, 3).join(", ");
    const more = filter.values.length > 3 ? ` +${filter.values.length - 3}` : "";
    return `${filter.exclude ? "Excluding " : ""}${shown}${more}`;
  }
  if (filter.kind === "quantitative") {
    const low = filter.min ?? null;
    const high = filter.max ?? null;
    if (low !== null && high !== null) return `${low} to ${high}`;
    if (low !== null) return `At least ${low}`;
    return `At most ${high}`;
  }
  const labels: Record<string, string> = {
    last_n_days: `Last ${filter.n} days`,
    last_n_months: `Last ${filter.n} months`,
    last_n_years: `Last ${filter.n} years`,
    month_to_date: "Month to date",
    year_to_date: "Year to date",
  };
  return labels[filter.range];
}

/** Add or replace a calculated field, renaming every pill that used it. */
export function upsertCalculation(
  spec: WorksheetSpec,
  calc: CalculatedField,
  previousName?: string,
): WorksheetSpec {
  const calculations = [...(spec.calculations ?? [])];
  const at = calculations.findIndex((item) => item.name === (previousName ?? calc.name));
  if (at >= 0) calculations[at] = calc;
  else calculations.push(calc);
  if (!previousName || previousName === calc.name) return { ...spec, calculations };
  const rename = (pill: FieldRef | null | undefined) =>
    pill && pill.field === previousName ? { ...pill, field: calc.name } : pill ?? null;
  const pattern = new RegExp(`\\[${escapeRegExp(previousName.replace(/\]/g, "]]"))}\\]`, "g");
  const replacement = `[${calc.name.replace(/\]/g, "]]")}]`;
  return {
    ...spec,
    calculations: calculations.map((item) =>
      item.name === calc.name ? item : { ...item, formula: item.formula.replace(pattern, () => replacement) },
    ),
    columns: spec.columns.map((pill) => rename(pill) as FieldRef),
    rows: spec.rows.map((pill) => rename(pill) as FieldRef),
    marks: {
      ...spec.marks,
      color: rename(spec.marks.color),
      size: rename(spec.marks.size),
      text: rename(spec.marks.text),
      detail: rename(spec.marks.detail),
      angle: rename(spec.marks.angle),
    },
    filters: (spec.filters ?? []).map((item) =>
      item.field === previousName ? { ...item, field: calc.name } : item,
    ),
  };
}

/** Remove a calculation and every pill or filter that used it. */
export function removeCalculation(spec: WorksheetSpec, name: string): WorksheetSpec {
  const drop = (pill: FieldRef | null | undefined) => (pill && pill.field === name ? null : pill ?? null);
  return {
    ...spec,
    calculations: (spec.calculations ?? []).filter((item) => item.name !== name),
    columns: spec.columns.filter((pill) => pill.field !== name),
    rows: spec.rows.filter((pill) => pill.field !== name),
    marks: {
      ...spec.marks,
      color: drop(spec.marks.color),
      size: drop(spec.marks.size),
      text: drop(spec.marks.text),
      detail: drop(spec.marks.detail),
      angle: drop(spec.marks.angle),
    },
    filters: (spec.filters ?? []).filter((item) => item.field !== name),
  };
}

/** Calculations other fields depend on cannot be deleted silently. */
export function calculationDependents(spec: WorksheetSpec, name: string): string[] {
  const token = `[${name.replace(/\]/g, "]]")}]`.toLowerCase();
  return (spec.calculations ?? [])
    .filter((item) => item.name !== name && item.formula.toLowerCase().includes(token))
    .map((item) => item.name);
}

export function allPills(spec: WorksheetSpec): FieldRef[] {
  const marks = spec.marks;
  return [
    ...spec.columns,
    ...spec.rows,
    ...[marks.color, marks.size, marks.text, marks.detail, marks.angle].filter(
      (pill): pill is FieldRef => Boolean(pill),
    ),
  ];
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Lay out a Show Me pick from the data pane's dimensions and measures. */
export function applyShowMe(working: WorksheetSpec, chartId: string, fields: DataPaneField[]): WorksheetSpec {
  const pills = fields.filter((field) => field.level === "row").map(pillFromField);
  const dims = pills.filter((pill) => !isMeasure(pill));
  const dates = dims.filter((pill) => pill.date_part);
  const measures = pills.filter((pill) => isMeasure(pill) && pill.aggregation === "sum");
  const axis = dates[0] ?? dims[0];
  const colorDim = dims.find((dimension) => dimension !== axis && !dimension.date_part);
  const base: WorksheetSpec = {
    ...working,
    columns: axis ? [axis] : [],
    rows: measures.slice(0, chartId === "bar" && measures.length > 1 ? 2 : 1),
    marks: emptyMarks("bar"),
    sort: [],
    table_calcs: [],
  };
  if (chartId === "line" || chartId === "area") {
    const timeline = axis?.date_part ? { ...axis, discrete: false, date_part: "month" as const, field_type: "temporal" as const } : axis;
    return { ...base, columns: timeline ? [timeline] : [], rows: measures.slice(0, 1), marks: { ...emptyMarks(chartId), color: colorDim ?? null } };
  }
  if (chartId === "pie") {
    const slice = colorDim ?? axis ?? null;
    return {
      ...base,
      columns: slice ? [slice] : [],
      rows: measures.slice(0, 1),
      marks: { ...emptyMarks("pie"), color: slice, angle: measures[0] ?? null },
    };
  }
  if (chartId === "scatter") {
    return {
      ...base,
      columns: measures.slice(0, 1),
      rows: measures.slice(1, 2).length ? measures.slice(1, 2) : measures.slice(0, 1),
      marks: { ...emptyMarks("circle"), detail: colorDim ?? dims[0] ?? null },
    };
  }
  if (chartId === "text_table" || chartId === "heatmap") {
    return {
      ...base,
      columns: dims.slice(0, 1),
      rows: dims.slice(1, 2),
      marks: {
        ...emptyMarks("text"),
        text: measures[0] ?? null,
        color: chartId === "heatmap" ? measures[0] ?? null : null,
      },
    };
  }
  if (chartId === "bar_stacked" || chartId === "bar_side") {
    return { ...base, rows: measures.slice(0, 1), marks: { ...emptyMarks("bar"), color: colorDim ?? null } };
  }
  return base;
}

// ── History ────────────────────────────────────────────────────

export type History = { past: WorksheetSpec[]; present: WorksheetSpec; future: WorksheetSpec[] };

const HISTORY_LIMIT = 50;

export function historyOf(spec: WorksheetSpec): History {
  return { past: [], present: spec, future: [] };
}

export type HistoryAction =
  | { type: "apply"; spec: WorksheetSpec }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "reset"; spec: WorksheetSpec };

export function historyReducer(state: History, action: HistoryAction): History {
  if (action.type === "reset") return historyOf(action.spec);
  if (action.type === "apply") {
    if (action.spec === state.present) return state;
    return {
      past: [...state.past, state.present].slice(-HISTORY_LIMIT),
      present: action.spec,
      future: [],
    };
  }
  if (action.type === "undo") {
    const previous = state.past[state.past.length - 1];
    if (!previous) return state;
    return { past: state.past.slice(0, -1), present: previous, future: [state.present, ...state.future] };
  }
  const next = state.future[0];
  if (!next) return state;
  return { past: [...state.past, state.present], present: next, future: state.future.slice(1) };
}
