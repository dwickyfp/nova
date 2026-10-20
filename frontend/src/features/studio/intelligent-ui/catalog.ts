import {
  action,
  createLibrary,
  defineComponent,
  defineFunction,
  markReactive,
} from "@openuidev/lang-core";
import { z } from "zod";
import { uiFunctions } from "./functions";
import { mapOptionsSchema } from "./map-options";

export const UI_CATALOG_VERSION = "nova-openui-3";
export const MAX_PROGRAM_BYTES = 64000;
export const MAX_STATE_BYTES = 16384;
export type EvidenceRows = Record<string, Record<string, unknown>[]>;
const text = z.string().max(16000);
const short = z.string().max(256);
const num = z.number().finite();
const value = z.unknown();
const children = z.array(value).max(100);
const optionalText = text.optional();
const tone = z
  .enum(["default", "muted", "primary", "success", "warning", "danger", "info"])
  .default("default");
const opts = z
  .array(z.union([short, z.object({ label: short, value: short })]))
  .max(100);
const rows = z.array(z.record(z.string(), value)).max(1000);
const childProps = { children, gap: num.min(0).max(12).default(3) };
const bound = z.unknown();
markReactive(bound);
// `.optional()` returns a new schema, which must be marked reactive itself.
const optionalBound = bound.optional();
markReactive(optionalBound);
const inputProps = {
  value: bound,
  label: short,
  placeholder: short.optional(),
  required: z.boolean().default(false),
};

export const componentDefinitions = [
  {
    name: "Kpi",
    description:
      "One verified metric from the first result row. Business data must bind @Evidence(id).",
    props: z.object({
      data: rows,
      column: short,
      label: short,
      digits: num.int().min(0).max(10).default(2),
    }),
  },
  {
    name: "Col",
    description: "Vertical layout. Root must be Col.",
    props: z.object(childProps),
  },
  {
    name: "Row",
    description: "Wrapping row; stacks on narrow screens.",
    props: z.object(childProps),
  },
  {
    name: "Box",
    description: "Padded section.",
    props: z.object({ children, tone }),
  },
  {
    name: "Grid",
    description: "Responsive grid with up to four columns.",
    props: z.object({
      children,
      columns: num.int().min(1).max(4).default(2),
      gap: childProps.gap,
    }),
  },
  {
    name: "Card",
    description: "Titled editorial section without an outer frame. Prefer direct prose and visuals; use Form or Box for related inputs.",
    props: z.object({ title: short, children, description: optionalText }),
  },
  {
    name: "Carousel",
    description: "Previous/next pages.",
    props: z.object({ children, label: short.default("Pages") }),
  },
  {
    name: "Popover",
    description: "Expandable details.",
    props: z.object({ label: short, children }),
  },
  { name: "Divider", description: "Section separator.", props: z.object({}) },
  {
    name: "Spacer",
    description: "Small vertical spacing.",
    props: z.object({ size: num.min(0).max(8).default(2) }),
  },
  ...["Title", "Text", "Caption", "Label", "Badge"].map((name) => ({
    name,
    description: `${name} text, using Nova tokens.`,
    props: z.object({ text, tone }),
  })),
  {
    name: "Link",
    description: "Public HTTPS link.",
    props: z.object({ text: short, url: text }),
  },
  {
    name: "List",
    description: "List of content.",
    props: z.object({ children, ordered: z.boolean().default(false) }),
  },
  {
    name: "ListItem",
    description: "List entry.",
    props: z.object({ children }),
  },
  {
    name: "Markdown",
    description: "Markdown prose.",
    props: z.object({ text }),
  },
  {
    name: "Quote",
    description: "Quotation and optional attribution.",
    props: z.object({ text, source: short.optional() }),
  },
  {
    name: "Code",
    description: "Scrollable code without execution.",
    props: z.object({ code: text, language: short.optional() }),
  },
  {
    name: "Math",
    description: "LaTeX math, with no HTML trust.",
    props: z.object({ formula: text }),
  },
  {
    name: "Button",
    description: "Local @Set/@Reset or @ToAssistant action. id must be unique.",
    props: z.object({
      label: short,
      action: action(),
      id: short,
      variant: z.enum(["default", "outline", "secondary"]).default("default"),
    }),
  },
  {
    name: "IconButton",
    description: "Accessible labelled icon action, unique id.",
    props: z.object({
      label: short,
      action: action(),
      id: short,
      icon: short.default("ArrowRight"),
    }),
  },
  {
    name: "Pressable",
    description: "A labelled button wrapping content, unique id.",
    props: z.object({ label: short, action: action(), id: short, children }),
  },
  {
    name: "Input",
    description: "Reactive text or numeric input; first arg usually $variable.",
    props: z.object({
      ...inputProps,
      type: z.enum(["text", "number", "email"]).default("text"),
    }),
  },
  {
    name: "TextArea",
    description: "Reactive multiline input.",
    props: z.object(inputProps),
  },
  ...["Select", "SegmentedControl", "RadioGroup"].map((name) => ({
    name,
    description: "Choose an option, bound to a $variable.",
    props: z.object({ value: bound, label: short, options: opts }),
  })),
  {
    name: "Slider",
    description: "Reactive number within bounds.",
    props: z.object({
      value: bound,
      label: short,
      min: num,
      max: num,
      step: num.positive().default(1),
    }),
  },
  {
    name: "Checkbox",
    description: "Reactive checkbox.",
    props: z.object({ value: bound, label: short }),
  },
  {
    name: "DatePicker",
    description: "Reactive ISO date.",
    props: z.object({ value: bound, label: short }),
  },
  {
    name: "Timer",
    description: "Start, pause and reset countdown; seconds are local state.",
    props: z.object({ value: bound, label: short.default("Timer") }),
  },
  {
    name: "Form",
    description:
      "Group fields; submit via Button @ToAssistant. No direct mutations.",
    props: z.object({ name: short, children }),
  },
  {
    name: "SearchImage",
    description:
      "Public Wikimedia photo search. Literal public terms only; no evidence or form variables.",
    props: z.object({ query: short, alt: short }),
  },
  {
    name: "Image",
    description: "Public Wikimedia upload image only, with alt text.",
    props: z.object({ url: text, alt: short, caption: short.optional() }),
  },
  {
    name: "Icon",
    description: "Named Nova icon.",
    props: z.object({ name: short, label: short.optional() }),
  },
  {
    name: "Table",
    description:
      "Records with optional selected columns. Business data must use @Evidence(id).",
    props: z.object({
      data: rows,
      columns: z.array(short).max(50).optional(),
      title: short.optional(),
    }),
  },
  {
    name: "Chart",
    description:
      "Bar, line, area, pie, donut or scatter chart of records. Business data must bind @Evidence(id).",
    props: z.object({
      data: rows,
      x: short,
      y: short,
      type: z
        .enum(["bar", "line", "area", "pie", "donut", "scatter"])
        .default("bar"),
      title: short.optional(),
      series: z.array(short).max(8).optional(),
      selected: bound.optional(),
    }),
  },
  {
    name: "GeoMap",
    description: "Governed business map. data must bind @Evidence(id). Use longitude/latitude columns or a geometry column containing WKT or GeoJSON Point, LineString or Polygon. Coordinates are WGS84 longitude first. selected binds a $variable: zero-based index into currently displayed rows, -1 for none. Use deterministic filters/sorts, never Shuffle/Pick/Random. Use a separate Button @ToAssistant for drill-down; Nova resolves the selected row.",
    props: z.object({ data: rows, longitude: short.optional(), latitude: short.optional(), geometry: short.optional(), label: short.optional(), title: short.default("Locations"), selected: bound, options: mapOptionsSchema.optional(), viewport: optionalBound }),
  },
  {
    name: "Map",
    description:
      "Public itinerary map. Static public locations only; driving route optional.",
    props: z.object({
      markers: children,
      route: z.boolean().default(false),
      label: short.default("Map"),
      options: mapOptionsSchema.optional(),
      viewport: optionalBound,
      routeSelected: optionalBound,
    }),
  },
  {
    name: "MapMarker",
    description:
      "Public place. Static latitude/longitude, no business bindings.",
    props: z.object({
      latitude: num.min(-90).max(90),
      longitude: num.min(-180).max(180),
      label: short,
    }),
  },
  {
    name: "YouTube",
    description: "Open a public YouTube video without embedded tracking.",
    props: z.object({
      videoId: z.string().regex(/^[a-zA-Z0-9_-]{11}$/),
      title: short,
    }),
  },
  {
    name: "Flashcard",
    description: "Flip between question and answer.",
    props: z.object({ question: text, answer: text }),
  },
  {
    name: "Celebration",
    description: "A short success announcement; respects reduced motion.",
    props: z.object({ text: short }),
  },
  {
    name: "Svg",
    description: "Responsive vector diagram. Coordinates use its viewBox.",
    props: z.object({
      children,
      viewBox: short.default("0 0 640 360"),
      label: short,
    }),
  },
  {
    name: "G",
    description: "SVG group.",
    props: z.object({ children, transform: short.optional() }),
  },
  {
    name: "Circle",
    description: "SVG circle.",
    props: z.object({ cx: num, cy: num, r: num.min(0), tone }),
  },
  {
    name: "Ellipse",
    description: "SVG ellipse.",
    props: z.object({ cx: num, cy: num, rx: num.min(0), ry: num.min(0), tone }),
  },
  {
    name: "Rect",
    description: "SVG rectangle.",
    props: z.object({
      x: num,
      y: num,
      width: num.min(0),
      height: num.min(0),
      tone,
      radius: num.min(0).default(4),
    }),
  },
  {
    name: "Line",
    description: "SVG line.",
    props: z.object({ x1: num, y1: num, x2: num, y2: num, tone }),
  },
  {
    name: "Path",
    description: "SVG path.",
    props: z.object({ d: text, tone }),
  },
  ...["Polyline", "Polygon"].map((name) => ({
    name,
    description: "SVG points.",
    props: z.object({ points: text, tone }),
  })),
  {
    name: "SvgText",
    description: "SVG label.",
    props: z.object({
      x: num,
      y: num,
      text: short,
      tone: z.enum(["default", "on-primary"]).default("default"),
    }),
  },
  {
    name: "HtmlView",
    description:
      "Self-contained HTML/canvas with inline scripts only. No network, parent access or business evidence. Include accessible controls and a text description.",
    props: z.object({
      html: z.string().max(32000),
      title: short,
      height: num.int().min(120).max(800).default(360),
    }),
  },
];

export function createSchemaLibrary(evidence: EvidenceRows = {}) {
  return createLibrary({
    root: "Col",
    components: componentDefinitions.map((d) =>
      defineComponent({ ...d, component: () => null }),
    ),
    functions: [
      ...uiFunctions,
      defineFunction({
        name: "Evidence",
        description:
          "Read immutable verified result records by exact evidence id. Local filters/aggregates cover only these rows.",
        params: z.object({ id: short }),
        fn: (p) => evidence[p.id] ?? null,
      }),
    ],
  });
}

export const uiPromptRules = [
  "Return short useful Markdown explaining the answer, then exactly one fenced ```openui program, then optional <claims> as usual. Markdown must independently explain the answer when the UI is unavailable.",
  "Use one root = Col([...]) statement. Declare $state variables with literal primitive/list defaults only, never expressions or references. Use regular variables without $ for derived expressions, e.g. weights=[$a,$b]; total=@Sum(weights). Inputs bind state variables and must affect displayed content or a form action.",
  "Every action component has a unique id. Use @ToAssistant for new data or Nova actions, @Set/@Reset for local changes. Query, Mutation and @Run are prohibited. Never invent a tool or direct SQL endpoint.",
  "Conditional UI branches may reveal controls, but every branch must be valid and action IDs must remain unique across the whole program. Each may repeat passive text/layout only; declare controls, media and business visuals explicitly.",
  'Business tables/charts bind @Evidence("exact-id") or local filters of it; never transcribe rows or KPI values. Keep verified values immutable. Every aggregate/filter is labelled "Within these result rows"; the result may be limited.',
  "Business result transforms are Filter, Sort, Take, Slice, Unique, Reverse, Shuffle, Top, Between and Pivot. Concat may only combine verified result sets. Never append state or constructed rows to business results.",
  "Simulations and calculator inputs must be clearly labelled assumptions. Do not call assumptions measured business facts. Do not put business values in HTML, SVG shapes or public media.",
  "Inputs and filters are saved to the conversation in Nova as they change. Local calculations do not make a model request. Continuing to the assistant sends submitted values to the configured model provider. Never claim input stays only in the browser or nothing is sent while typing.",
  "Public media queries/URLs/map positions must be static literal public content. Never use form state, evidence, internal addresses or credentials in public media or links.",
  "HtmlView is offline and isolated: inline JavaScript/CSS only, no external scripts, fetch, storage, parent, navigation or business data. Provide keyboard controls and a fallback description.",
  "HtmlView receives Nova CSS variables. For canvas paint use getComputedStyle(canvas).color; its default black paint may be invisible in dark mode. Avoid dynamic property names and Object introspection/assignment in scripts.",
  "Compose answers inline: avoid wrapping the whole answer in Card. Use Card only for a related input group. Choose a visual when it helps the task; plain questions need prose. Do not repeat the Markdown answer inside the UI.",
  'Map options are a literal object: controls (map action buttons), advanced, editable (public assumptions only), simulation (public only), popupFields (up to 8 columns), layer (geometry/route/arc), progress (business progress column 0..1), toLongitude/toLatitude (arc destination columns). Append options and optional $viewport after existing arguments. $viewport starts null; Map also accepts $routeSelected after it. Never put data, URLs, functions, or expressions in options. Business route requires a supplied LineString; arcs connect evidence endpoints, not road paths or travel measurements.',
  "GeoMap is for verified business locations only. Never send business coordinates to Map, routing, images or external URLs. Select column names from actual evidence. Use Geometry WKT or GeoJSON only when supplied by the query; never invent a boundary.",
  "Apply the same local filter to a business map and its companion table. Map selection is an index into that filtered result, not the original evidence. Assistant actions receive selected_rows_snapshot resolved by Nova; do not ask the user to interpret internal indices.",
  "Use Nova token tones, no custom CSS colors. Keep prose short and responsive; make all controls labelled and usable with keyboard.",
];

export const uiExamples = [
  { purpose: "Verified route geometry and observed progress; no new routing request.", program: '$selected=-1\n$viewport=null\nroot=Col([GeoMap(@Evidence("result-id"),null,null,"geometry","name","Routes",$selected,{layer:"route",progress:"progress",advanced:true},$viewport)])' },
  { purpose: "Origin–destination relationships, not road distances.", program: '$selected=-1\nroot=Col([GeoMap(@Evidence("result-id"),"longitude","latitude",null,"name","Connections",$selected,{layer:"arc",toLongitude:"destination_longitude",toLatitude:"destination_latitude"})])' },
  {
    purpose: "Verified comparison. Use the actual result id and column names.",
    program: 'root = Col([Chart(@Evidence("result-id"), "month", "revenue", "line", "Revenue")])',
  },
  {
    purpose: "Public places. Only static public locations may use Map.",
    program: 'root = Col([Map([MapMarker(-6.1754, 106.8272, "Monas")], false, "Jakarta")])',
  },
  {
    purpose: "Business map. Selection is a row index within these displayed result rows.",
    program: '$selected = -1\nroot = Col([GeoMap(@Evidence("result-id"), "longitude", "latitude", null, "branch", "Branches", $selected), Button("Investigate selected branch", @ToAssistant("Investigate the selected result row"), "branch-detail")])',
  },
  {
    purpose: "Assumption calculator. Do not call this a measured business result.",
    program: '$price = 100\n$quantity = 2\nroot = Col([Text("Assumption calculator"), Input($price, "Price", null, false, "number"), Input($quantity, "Quantity", null, false, "number"), Text("Estimated total: " + @Round($price * $quantity))])',
  },
  {
    purpose: "Follow-up form. Forms do not execute tools directly.",
    program: '$question = ""\nroot = Col([Form("followup", [TextArea($question, "Question", null, true), Button("Ask Nova", @ToAssistant("Answer my follow-up question"), "ask")])])',
  },
];
export const uiPromptExamples = uiExamples.map(({ purpose, program }) => `${purpose}\n${program}`);
