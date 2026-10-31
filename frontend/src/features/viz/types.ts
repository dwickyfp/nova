/** Viz-core contracts, mirroring backend/app/modules/viz/spec.py. */

export type FieldType = "nominal" | "ordinal" | "quantitative" | "temporal";

export type Aggregation =
  | "sum"
  | "avg"
  | "median"
  | "min"
  | "max"
  | "count"
  | "distinct_count"
  | "stdev"
  | "stdevp"
  | "var"
  | "varp"
  | "attr"
  /** A pill over a calculated field that is already aggregated. */
  | "agg";

export type DatePart = "year" | "quarter" | "month" | "week" | "day";

export type MarkType =
  | "bar"
  | "line"
  | "area"
  | "circle"
  | "square"
  | "tick"
  | "pie"
  | "text";

export type RelativeDateRange =
  | "last_n_days"
  | "last_n_months"
  | "last_n_years"
  | "month_to_date"
  | "year_to_date";

export type Filter =
  | {
      kind: "quantitative";
      field: string;
      min?: number | string | null;
      max?: number | string | null;
      /** Set for a measure filter on the aggregated value per mark. */
      aggregation?: Aggregation | null;
      context?: boolean;
    }
  | {
      kind: "categorical";
      field: string;
      values: string[];
      exclude?: boolean;
      null_included?: boolean | null;
      context?: boolean;
    }
  | {
      kind: "relative_date";
      field: string;
      range: RelativeDateRange;
      n: number;
      context?: boolean;
    };

/** A chart artifact's worksheet. `source` is absent in chat previews until saved. */
/** One entry of the authoring data pane. */
export type ZoneRect = { x: number; y: number; w: number; h: number };

export type Zone = {
  zone_id: string;
  kind: "sheet" | "text" | "filter" | "spacer";
  rect: ZoneRect;
  artifact_id?: string | null;
  text?: { content: string; style: "title" | "body" } | null;
  filter?: { field: string; apply_to: string[] } | null;
};

export type DashboardSpec = {
  version: 1;
  title?: string;
  size?: { mode: "auto" | "fixed"; width?: number | null; height?: number | null };
  zones: Zone[];
  actions?: {
    action_id: string;
    kind: "filter" | "highlight";
    source_zone: string;
    target_zones: string[];
    field?: string | null;
    run_on: "select" | "menu";
  }[];
};

/** The compute view the renderer consumes (backend/app/modules/viz/presmodel.py). */
export type PresModelColumn = {
  name: string;
  field: string;
  field_type: FieldType;
  role: "dimension" | "measure";
  aggregation?: Aggregation | null;
  discrete: boolean;
  label: string;
  date_part?: DatePart | null;
};

export type PresModel = {
  version: number;
  title: string;
  mark: MarkType;
  columns: PresModelColumn[];
  rows: (string | number | null)[][];
  row_count: number;
  axes: {
    x: { field: string; label: string; field_type: FieldType; discrete: boolean } | null;
    y: { field: string; label: string; field_type: FieldType; discrete: boolean } | null;
  };
  legend: {
    color: { field: string; kind: "categorical" | "quantitative"; domain: (string | number)[] } | null;
  };
  totals: Record<string, number | null>;
  truncated?: boolean;
};

export type DashboardZoneResult = {
  zone_id: string;
  artifact_id: string | null;
  title?: string;
  presmodel?: PresModel;
  sql?: string;
  row_count?: number;
  error?: string;
};

export type DashboardComputeResponse = {
  title: string;
  zones: DashboardZoneResult[];
};

/** A dashboard artifact created mid-conversation by dashboard_build. */
export type DashboardArtifactBlock = {
  artifact_id: string;
  title: string;
  charts: { artifact_id: string; title: string; chart_type?: string }[];
  fallback: string;
};
