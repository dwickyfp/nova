/**
 * The Studio layout language: the one schema source for page-like answers.
 *
 * A layout is an OpenUI Lang program (`name = Component(args)` per line) that
 * arranges registry components into a page: a header, sections, KPI tiles,
 * native charts, short narrative and local what-if controls. It is one
 * registry component (`Layout`, catalog `nova-layout-1`), not a second runtime:
 * charts inside it are always native `DatavizChart`s bound to a governed draft,
 * and KPIs are `KpiTiles`.
 *
 * Two dialects share these schemas:
 * - the model writes `Chart(...)`, `Table(...)` and `@Value(...)` against
 *   verified results;
 * - the server checks every statement as it streams and rewrites those into
 *   `DatavizChart`, `ResultTable` and plain numbers before the browser sees
 *   them, so every figure on the page comes from verified rows.
 *
 * `componentContract()` exports this module to the backend
 * (`ui_components.json`, key `layout`). The server validates each statement
 * against it and gives the model the prompt built here. Keep the module free
 * of React: the generator bundles it for Node.
 */
import { markReactive } from "@openuidev/lang-core";
import { z } from "zod";

export const LAYOUT_CATALOG = "nova-layout-1";
export const LAYOUT_ROOT = "Page";

/** Who may write a component: the model, the server's rewrite, or both. */
type Author = "model" | "server" | "both";

const id = z.string().regex(/^[A-Za-z0-9_.:-]{1,128}$/);
const short = z.string().max(160);
const title = z.string().min(1).max(160);
const column = z.string().min(1).max(256);
/** A list of child nodes: references to other statements or inline components. */
const children = z.array(z.unknown()).max(24);
const numberFormat = z.enum(["number", "currency", "percent"]);

const page = z.object({
  children: children.describe("The page's blocks in reading order: Header first, then sections."),
});
const header = z.object({
  title: title.describe("A short page title, e.g. 'Sales overview'."),
  subtitle: z.string().max(240).optional().describe("One sentence on what the page shows."),
});
const section = z.object({
  title: short.nullable().describe("The section heading, or null."),
  children,
  note: z.string().max(400).optional().describe("A short caption under the section."),
});
const row = z.object({ children: children.describe("Two or three blocks shown side by side on wide screens.") });
const kpiTiles = z.object({
  items: z.array(z.unknown()).min(1).max(6).describe("Metric(...) nodes."),
  title: short.optional(),
});
const metric = z.object({
  label: z.string().min(1).max(120),
  value: z
    .number()
    .nullable()
    .describe("A verified figure: @Value(...), a statement holding one, or arithmetic over them and $variables."),
  format: numberFormat.default("number"),
  delta: z
    .number()
    .nullable()
    .optional()
    .describe("Relative change as a fraction (0.12 is +12%), computed from verified figures. Shown as a coloured sublabel."),
  badge: z.string().max(80).optional().describe("A short sublabel such as '6 months' or 'Held constant'."),
  currency: z.string().regex(/^[A-Z]{3}$/).optional().describe("ISO code, only when the data states it."),
  decimals: z.number().int().min(0).max(6).optional(),
});
const chart = z.object({
  evidence: id.describe("The verified result id the chart draws."),
  visual: z.enum(["bar", "line", "area", "pie", "scatter"]),
  x: column.describe("The category or date column, exactly as in the result."),
  y: z.union([column, z.array(column).min(1).max(4)]).describe("The measure column(s)."),
  title,
  date_grain: z.enum(["day", "week", "month", "quarter", "year"]).optional(),
  color: column.optional().describe("A second category that splits the series."),
  top_n: z.number().int().min(1).max(50).optional(),
});
const datavizChart = z.object({
  title,
  /** A native `ChartDefinition`, checked by the server with the Dataviz contract. */
  chart: z.record(z.string(), z.unknown()),
  /** Signed, caller-bound reference to the chart's governed query. */
  draft_ref: z.string().min(8).max(2048),
});
const table = z.object({
  evidence: id,
  columns: z.array(column).max(12).nullable().optional().describe("Columns to show; null for all."),
  title: short.optional(),
});
const resultTable = z.object({
  title: short.nullable(),
  columns: z.array(column).max(12),
  /** Up to 50 rows of the result, already redacted by the server. */
  rows: z.array(z.array(z.unknown())).max(50),
});
const text = z.object({
  markdown: z.string().min(1).max(4000).describe("Short narrative in Markdown. State only verified figures."),
});
const controls = z.object({
  title: title.describe("e.g. 'What-if scenario'."),
  children: children.describe("Slider, Segmented and Toggle nodes."),
  note: z.string().max(400).optional().describe("What the controls change, e.g. 'Order volume stays constant.'"),
});
/** A `$variable` the control reads and writes; the renderer keeps it bound. */
function binding() {
  const schema = z.unknown().refine((value) => value !== undefined).describe("A $variable.");
  markReactive(schema);
  return schema;
}
const slider = z.object({
  label: z.string().min(1).max(120),
  value: binding(),
  min: z.number(),
  max: z.number(),
  step: z.number().positive().optional(),
  format: numberFormat.optional().describe("How the value reads: percent shows 10 as 10%."),
});
const option = z.union([short, z.object({ label: short, value: z.union([short, z.number()]) })]);
const segmented = z.object({
  label: z.string().min(1).max(120),
  value: binding(),
  options: z.array(option).min(2).max(6),
});
const toggle = z.object({ label: z.string().min(1).max(120), value: binding() });

const latitude = z.number().min(-90).max(90);
const longitude = z.number().min(-180).max(180);
const dataMap = z.object({
  evidence: id.describe("The verified result whose rows are mapped."),
  latitude: column.nullable().optional().describe("Latitude column; null to find it."),
  longitude: column.nullable().optional().describe("Longitude column; null to find it."),
  label: column.nullable().optional().describe("Column naming each place."),
  measure: column.nullable().optional().describe("A figure shown for each place."),
  title: short.optional(),
});
/** The map a `DataMap` became: the shared GeoMap props, rows read by the server. */
const geoMap = z.object({ map: z.record(z.string(), z.unknown()) });
const place = z.object({
  label: z.string().min(1).max(120),
  lat: latitude,
  lon: longitude,
});
const routeMap = z.object({
  title,
  places: z.array(place).min(1).max(25).describe("Public places in visiting order: {label, lat, lon}. Name each city or landmark."),
  route: z.boolean().optional().describe("Draw the road route through the places in order (default false)."),
  optimize: z.boolean().optional().describe("Visit the places in the shortest road order, starting from the first."),
});
const checkedPlace = place.extend({ check: z.enum(["verified", "corrected", "approximate"]) });
const placeMap = z.object({
  title,
  places: z.array(checkedPlace).min(1).max(25),
  /** Road routes the server fetched: the first is the fastest; up to three alternatives. */
  routes: z
    .array(
      z.object({
        /** The road line as an encoded polyline (five decimals), at most 5,000 positions in all. */
        line: z.string().min(1).max(60_000),
        distance_m: z.number().min(0),
        duration_s: z.number().min(0),
      }),
    )
    .max(3),
  /** Why no road route is shown, when one was asked for. */
  unavailable: z.string().max(240).nullable().optional(),
  optimized: z.boolean().optional(),
});
const image = z.object({
  url: z.string().url().max(2048).describe("An https://upload.wikimedia.org/ image."),
  alt: z.string().min(1).max(240),
});
const searchImage = z.object({
  query: z.string().min(1).max(200).describe("What the picture shows, e.g. 'Borobudur temple'."),
  alt: z.string().min(1).max(240),
});
const picture = z.object({
  url: z.string().max(2048),
  alt: z.string().min(1).max(240),
  source: z.string().max(2048),
});

type Entry = { author: Author; description: string; props: z.ZodObject; reactive?: readonly string[] };

/** Components in prompt order. Positional arguments follow each schema's key order. */
export const LAYOUT_COMPONENTS = {
  Page: { author: "both", description: "The root: root = Page([...]).", props: page },
  Header: { author: "both", description: "Page title and a one-line subtitle.", props: header },
  Section: { author: "both", description: "A titled group of blocks.", props: section },
  Row: { author: "both", description: "Blocks side by side, e.g. two charts.", props: row },
  KpiTiles: { author: "both", description: "A row of headline figures.", props: kpiTiles },
  Metric: { author: "both", description: "One KPI tile inside KpiTiles.", props: metric },
  Chart: {
    author: "model",
    description: "A native chart of one verified result. Nova binds it to the result's governed query.",
    props: chart,
  },
  DatavizChart: { author: "server", description: "A native Dataviz chart.", props: datavizChart },
  Table: { author: "model", description: "Rows of one verified result (up to 50).", props: table },
  ResultTable: { author: "server", description: "Rows of a verified result.", props: resultTable },
  Text: { author: "both", description: "A short paragraph of narrative.", props: text },
  DataMap: {
    author: "model",
    description: "A map of a verified result's located rows (latitude/longitude columns).",
    props: dataMap,
  },
  GeoMap: { author: "server", description: "A map of verified rows.", props: geoMap },
  RouteMap: {
    author: "model",
    description: "Public places (cities, landmarks) on a map, optionally with the road route between them.",
    props: routeMap,
  },
  PlaceMap: { author: "server", description: "Checked public places and road routes.", props: placeMap },
  Image: { author: "model", description: "A Wikimedia Commons image by its upload URL.", props: image },
  SearchImage: { author: "model", description: "A public Wikimedia Commons image found by a search.", props: searchImage },
  Picture: { author: "server", description: "A checked public image.", props: picture },
  Controls: {
    author: "both",
    description: "A panel of what-if controls whose $variables drive Metric values below it.",
    props: controls,
  },
  Slider: { author: "both", description: "A numeric control.", props: slider, reactive: ["value"] },
  Segmented: { author: "both", description: "A choice between 2 to 6 options.", props: segmented, reactive: ["value"] },
  Toggle: { author: "both", description: "An on/off switch.", props: toggle, reactive: ["value"] },
} as const satisfies Record<string, Entry>;

export type LayoutComponentName = keyof typeof LAYOUT_COMPONENTS;

/** `@Value(...)`: the model's only way to put a verified figure on the page. */
export const valueFunction = z.object({
  evidence: id,
  column,
  aggregation: z.enum(["value", "sum", "avg", "min", "max", "count"]).default("value"),
  where: z
    .record(z.string(), z.union([z.string(), z.number(), z.boolean()]))
    .optional()
    .describe("Only rows whose columns equal these values, e.g. {month: \"2025-07\"}."),
});

const RULES = [
  "Write a layout only when the person asks for a dashboard, an overview, a page, a comparison of several figures, a what-if/scenario, a map, a route or a picture. Answer a single figure, a short list or a plain question in Markdown without a layout: one number never becomes a page.",
  "A layout replaces the Markdown answer. Write it in exactly one ```openui fence. After the fence write at most one plain sentence; put the narrative in Text blocks inside the page.",
  "Start with `root = Page([...])`, then define each referenced statement on its own line, top to bottom in reading order: Header first, then sections. Nova shows each statement as soon as its line is complete, so define blocks in the order they appear.",
  "Every figure comes from a verified result: write @Value(\"result-id\", \"column\", \"sum\") or a statement holding one, never a number you typed. Derived figures (differences, shares, scenarios) are arithmetic over those values and $variables, e.g. scenario = base * (1 + $uplift / 100). Use exact result ids and column names.",
  "Charts are Chart(\"result-id\", \"line\", \"month\", \"revenue\", \"Revenue by month\"): name the visual and exact result columns. Each chart draws one verified result; do not chart a scenario.",
  "Metric(label, value, format, delta, badge): delta is a fraction computed from verified values, e.g. (jul - jun) / jun. badge is a few words such as \"6 months\". Use format \"currency\" with a currency code only when the data states one; otherwise use \"number\". Do not remark on a missing currency.",
  "Comparing periods or groups: show the latest period's figures once, each with its delta against the earlier one and a badge such as \"vs. June\", rather than one row of tiles per period. Chart the full series beside them.",
  "What-if: declare each control's state with a literal default ($uplift = 10), put Slider/Segmented/Toggle nodes in a Controls panel above the figures they change, and label scenario values as scenarios. Controls only recompute values already on the page; they never fetch data.",
  "Maps: DataMap(\"result-id\", \"latitude\", \"longitude\", \"city\", \"revenue\", \"Revenue by city\") maps verified rows that have coordinates; when the result has none, name its city column instead, DataMap(\"result-id\", null, null, \"city\", \"revenue\", \"Revenue by city\"), and Nova places each row at that city's centre. RouteMap(title, [{label: \"Jakarta\", lat: -6.2088, lon: 106.8456}, ...], true) shows public places such as cities and, with true, the road route through them in order, with distance and travel time; add true as the fourth argument to visit them in the shortest order from the first. Use RouteMap only for public places by name, never for customer addresses or other private locations. A map asked for on its own is still one block: root = Page([map]).",
  "Images: SearchImage(\"Borobudur temple\", \"Borobudur temple at sunrise\") shows a public Wikimedia Commons picture; use one only when the person asks for a picture of a public place or thing.",
  "Keep it to one Header, at most four sections, at most six KPI tiles per row and at most three charts. Text blocks are one to three sentences and state only figures shown on the page.",
];

const EXAMPLE = [
  "root = Page([header, kpis, trend, scenario, outcome, summary])",
  'header = Header("Sales overview", "Revenue for June and July 2025, with a revenue what-if.")',
  'jun = @Value("evidence_1", "revenue", "value", {month: "2025-06"})',
  'jul = @Value("evidence_1", "revenue", "value", {month: "2025-07"})',
  'kpis = KpiTiles([Metric("July revenue", jul, "number", (jul - jun) / jun, "vs. June"), Metric("Orders", @Value("evidence_1", "orders", "value", {month: "2025-07"}), "number", null, "July")])',
  'trend = Section("Trend", [Chart("evidence_1", "line", "month", "revenue", "Revenue by month")])',
  "$uplift = 10",
  'scenario = Controls("What-if scenario", [Slider("Revenue change", $uplift, -30, 50, 5, "percent")], "The change applies to every order; order volume stays constant.")',
  'outcome = KpiTiles([Metric("Scenario July revenue", jul * (1 + $uplift / 100), "number", $uplift / 100, "Scenario")])',
  'summary = Text("One or two sentences on what the figures show, naming only figures on the page.")',
].join("\n");

/** The model's instructions for the layout language, built from the schemas above. */
export function layoutPrompt(): string {
  const signature = (name: string, props: z.ZodObject) => {
    const required = new Set((jsonSchema(props).required as string[] | undefined) ?? []);
    return `${name}(${Object.keys(props.shape)
      .map((key) => (required.has(key) ? key : `${key}?`))
      .join(", ")})`;
  };
  const components = Object.entries(LAYOUT_COMPONENTS)
    .filter(([, entry]) => entry.author !== "server")
    .map(([name, entry]) => `- ${signature(name, entry.props)}: ${entry.description}`);
  return [
    "<PAGE_LAYOUT>",
    "You can answer with a page: an OpenUI Lang program of `name = Component(args)` statements, one per line. Arguments are positional in the order shown; `?` marks optional ones.",
    ...RULES.map((rule) => `- ${rule}`),
    "Components:",
    ...components,
    `- @Value(evidence, column, aggregation?, where?): a verified figure from a result. aggregation: value (first matching row), sum, avg, min, max or count.`,
    "Example (use the real result ids and columns):",
    "```openui",
    EXAMPLE,
    "```",
    "</PAGE_LAYOUT>",
  ].join("\n");
}

function jsonSchema(schema: z.ZodType) {
  const { $schema: _schema, ...rest } = z.toJSONSchema(schema, {
    target: "draft-2020-12",
    io: "input",
    unrepresentable: "any",
  }) as Record<string, unknown>;
  return rest;
}

/** The exported layout contract, as written to the backend. */
export function layoutContract() {
  return {
    catalog: LAYOUT_CATALOG,
    root: LAYOUT_ROOT,
    components: Object.fromEntries(
      Object.entries(LAYOUT_COMPONENTS).map(([name, entry]) => [
        name,
        {
          author: entry.author,
          description: entry.description,
          props: jsonSchema(entry.props),
          ...("reactive" in entry ? { reactive: [...entry.reactive] } : {}),
        },
      ]),
    ),
    functions: { Value: { params: jsonSchema(valueFunction) } },
    prompt: layoutPrompt(),
  };
}
