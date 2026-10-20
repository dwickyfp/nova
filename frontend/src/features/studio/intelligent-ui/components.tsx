import { lazy, Suspense, useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  FormNameContext,
  useFormName,
  useRenderNode,
  useStateField,
  useTriggerAction,
} from "@openuidev/react-lang";
import {
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  Info,
  Play,
  Plus,
  RotateCcw,
} from "lucide-react";

import katex from "katex";
import "katex/dist/katex.min.css";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Markdown } from "@/features/assistant/markdown";
import { cn } from "@/lib/utils";
import { useTheme } from "@/context/theme-provider";
import { PublicMedia } from "./media";
import { UiRuntimeContext } from "./runtime-context";
const DataChart = lazy(() => import("./data-chart"));
const GeoMap = lazy(() => import("./geo-map"));
type Props = Record<string, unknown>;
const str = (p: Props, key: string) => String(p[key] ?? "");
const num = (p: Props, key: string, fallback = 0) =>
  typeof p[key] === "number" ? (p[key] as number) : fallback;
const tones: Record<string, string> = {
  default: "text-foreground",
  muted: "text-muted-foreground",
  primary: "text-primary dark:text-accent-foreground",
  success: "text-success-strong",
  warning: "text-warning-strong",
  danger: "text-destructive",
  info: "text-info-strong",
};
const svgTones: Record<string, string> = {
  default: "var(--muted)",
  muted: "var(--muted)",
  primary: "var(--primary)",
  success: "var(--success)",
  warning: "var(--warning)",
  danger: "var(--destructive)",
  info: "var(--info)",
};

function ActionControl({ p, children }: { p: Props; children?: ReactNode }) {
  const runtime = useContext(UiRuntimeContext);
  const trigger = useTriggerAction();
  const formName = useFormName();
  return (
    <Button
      type={formName ? "submit" : "button"}
      disabled={runtime.readOnly}
      variant={
        p.variant === "outline" || p.variant === "secondary"
          ? p.variant
          : "default"
      }
      className="min-h-11 max-w-full whitespace-normal border-muted-foreground [&_*]:text-inherit"
      onClick={(e) => {
        const form = e.currentTarget.closest("form");
        if (form && !form.reportValidity()) return;
        runtime.markAction(str(p, "id"));
        void trigger(
          str(p, "label"),
          formName,
          p.action as Parameters<typeof trigger>[2],
        );
      }}
    >
      {children ?? str(p, "label")}
    </Button>
  );
}

function Field({ name, p }: { name: string; p: Props }) {
  const id = useId();
  const field = useStateField("value", p.value);
  const runtime = useContext(UiRuntimeContext);
  const label = str(p, "label");
  const common = {
    id,
    disabled: runtime.readOnly,
    "aria-label": label,
    required: p.required === true,
  };
  if (name === "Checkbox")
    return (
      <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm">
        <input
          {...common}
          type="checkbox"
          checked={field.value === true}
          onChange={(e) => field.setValue(e.target.checked)}
          className="size-5 accent-primary focus-visible:outline-2 focus-visible:outline-ring"
        />
        {label}
      </label>
    );
  const options = Array.isArray(p.options)
    ? p.options.map((v) =>
        typeof v === "string"
          ? { label: v, value: v }
          : (v as { label: string; value: string }),
      )
    : [];
  const input =
    name === "TextArea" ? (
      <Textarea
        {...common}
        className="border-muted-foreground"
        placeholder={str(p, "placeholder")}
        value={String(field.value ?? "")}
        maxLength={4000}
        onChange={(e) => field.setValue(e.target.value)}
      />
    ) : ["Select", "RadioGroup", "SegmentedControl"].includes(name) ? (
      name === "Select" ? (
        <select
          {...common}
          className="min-h-11 w-full rounded-md border border-muted-foreground bg-background px-3 text-sm focus-visible:outline-2 focus-visible:outline-ring"
          value={String(field.value ?? "")}
          onChange={(e) => field.setValue(e.target.value)}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ) : (
        <div
          role="radiogroup"
          aria-label={label}
          className="flex flex-wrap gap-2"
        >
          {options.map((o) => (
            <label
              key={o.value}
              className="flex min-h-11 items-center gap-2 rounded-md border border-input px-3 text-sm"
            >
              <input
                disabled={runtime.readOnly}
                type="radio"
                name={id}
                checked={field.value === o.value}
                onChange={() => field.setValue(o.value)}
                className="accent-primary focus-visible:outline-2 focus-visible:outline-ring"
              />
              {o.label}
            </label>
          ))}
        </div>
      )
    ) : name === "Slider" ? (
      <div className="flex items-center gap-3">
        <input
          {...common}
          type="range"
          className="min-h-11 min-w-0 flex-1 accent-primary focus-visible:outline-2 focus-visible:outline-ring"
          min={num(p, "min")}
          max={num(p, "max", 100)}
          step={num(p, "step", 1)}
          value={Number(field.value ?? p.min)}
          onChange={(e) => field.setValue(Number(e.target.value))}
        />
        <output className="text-sm tabular-nums">
          {String(field.value ?? "")}
        </output>
      </div>
    ) : (
      <Input
        {...common}
        className="min-h-11 border-muted-foreground"
        type={name === "DatePicker" ? "date" : str(p, "type") || "text"}
        placeholder={str(p, "placeholder")}
        value={
          typeof field.value === "string" || typeof field.value === "number"
            ? field.value
            : ""
        }
        maxLength={4000}
        onChange={(e) =>
          field.setValue(
            p.type === "number"
              ? e.target.value === ""
                ? 0
                : Number(e.target.value)
              : e.target.value,
          )
        }
        onFocus={(e) => e.target.scrollIntoView({ block: "nearest" })}
      />
    );
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
        {p.required === true ? " *" : ""}
      </label>
      {input}
    </div>
  );
}

function Timer({ p }: { p: Props }) {
  const field = useStateField<number>("value", p.value as number);
  const [running, setRunning] = useState(false);
  const start = useRef<number | null>(null);
  const { readOnly } = useContext(UiRuntimeContext);
  useEffect(() => {
    if (start.current === null && typeof field.value === "number")
      start.current = field.value;
  }, [field.value]);
  useEffect(() => {
    if (!running || readOnly) return;
    const handle = window.setInterval(() => {
      field.setValue(Math.max(0, Number(field.value) - 1));
    }, 1000);
    return () => window.clearInterval(handle);
  }, [field, running, readOnly]);
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-sm">{str(p, "label")}</span>
      <output aria-live="off" className="font-mono tabular-nums">
        {Math.floor(Number(field.value) / 60)}:
        {String(Number(field.value) % 60).padStart(2, "0")}
      </output>
      <Button
        variant="outline"
        className="min-h-11 border-muted-foreground"
        disabled={readOnly || Number(field.value) <= 0}
        onClick={() => setRunning((v) => !v)}
      >
        {running ? "Pause" : "Start"}
      </Button>
      <Button
        variant="outline"
        className="min-h-11 border-muted-foreground"
        disabled={readOnly}
        onClick={() => {
          setRunning(false);
          field.setValue(start.current ?? 0);
        }}
      >
        Reset
      </Button>
    </div>
  );
}

function Pages({ p }: { p: Props }) {
  const render = useRenderNode();
  const [page, setPage] = useState(0);
  const children = Array.isArray(p.children) ? p.children : [];
  return (
    <section aria-label={str(p, "label")} className="space-y-3">
      {render(children[page])}
      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          aria-label="Previous page"
          className="min-h-11 border-muted-foreground"
          disabled={!page}
          onClick={() => setPage((v) => v - 1)}
        >
          <ChevronLeft />
        </Button>
        <span className="text-sm">
          {page + 1} / {children.length}
        </span>
        <Button
          variant="outline"
          aria-label="Next page"
          className="min-h-11 border-muted-foreground"
          disabled={page >= children.length - 1}
          onClick={() => setPage((v) => v + 1)}
        >
          <ChevronRight />
        </Button>
      </div>
    </section>
  );
}

function Flashcard({ p }: { p: Props }) {
  const [back, setBack] = useState(false);
  return (
    <button
      type="button"
      aria-label={back ? "Show question" : "Show answer"}
      aria-pressed={back}
      onClick={() => setBack((v) => !v)}
      className="min-h-32 w-full rounded-lg border border-muted-foreground bg-card p-5 text-left text-sm focus-visible:outline-2 focus-visible:outline-ring"
    >
      <span className="mb-2 block text-xs text-muted-foreground">
        {back ? "Answer" : "Question"} · Click to flip
      </span>
      {str(p, back ? "answer" : "question")}
    </button>
  );
}

export function DataTable({ p }: { p: Props }) {
  const data = Array.isArray(p.data) ? (p.data as Props[]) : [];
  const cols = Array.isArray(p.columns)
    ? p.columns.map(String)
    : Object.keys(data[0] ?? {});
  return (
    <section className="min-w-0">
      <p className="mb-2 text-sm font-medium">{str(p, "title")}</p>
      {data.length ? (
        <div
          className="max-w-full overflow-x-auto rounded-lg border border-border"
          tabIndex={0}
          aria-label={str(p, "title") || "Result table"}
        >
          <table className="w-full text-left text-sm">
            <thead className="bg-muted">
              <tr>
                {cols.map((c) => (
                  <th key={c} scope="col" className="px-3 py-2 font-medium">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((r, i) => (
                <tr key={i} className="border-t border-border">
                  {cols.map((c) => (
                    <td key={c} className="max-w-64 break-words px-3 py-2">
                      {typeof r[c] === "object" && r[c] !== null
                        ? JSON.stringify(r[c])
                        : String(r[c] ?? "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          No rows match these filters.
        </p>
      )}
    </section>
  );
}


function MathFormula({ p }: { p: Props }) {
  const result = useMemo(
    () =>
      katex.renderToString(str(p, "formula"), {
        throwOnError: false,
        trust: false,
        strict: "error",
        output: "mathml",
        maxExpand: 100,
      }),
    [p],
  );
  return (
    <div
      className="max-w-full overflow-x-auto text-sm"
      dangerouslySetInnerHTML={{ __html: result }}
    />
  );
}

function HtmlView({ p }: { p: Props }) {
  const { readOnly } = useContext(UiRuntimeContext);
  const { resolvedTheme } = useTheme();
  const readTokens = () => {
    const style = getComputedStyle(document.documentElement);
    return Object.fromEntries(
      [
        "foreground",
        "card",
        "primary",
        "primary-foreground",
        "border",
        "muted-foreground",
        "ring",
      ].map((name) => [name, style.getPropertyValue(`--${name}`).trim()]),
    );
  };
  const [tokens, setTokens] = useState(readTokens);
  useEffect(() => {
    const observer = new MutationObserver(() => setTokens(readTokens()));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, []);
  const html = str(p, "html");
  const srcDoc = useMemo(
    () =>
      `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; frame-src 'none'; font-src 'none'; form-action 'none'; base-uri 'none'"><meta name="viewport" content="width=device-width,initial-scale=1"><style>:root{color-scheme:${resolvedTheme};${Object.entries(
        tokens,
      )
        .map(([key, value]) => `--${key}:${value}`)
        .join(
          ";",
        )}}body{margin:12px;font:14px system-ui;overflow-wrap:anywhere;color:${tokens.foreground};background:${tokens.card}}canvas,svg,img{max-width:100%}button,input{max-width:100%;min-height:44px;border:1px solid ${tokens["muted-foreground"]};color:${tokens.foreground};background:${tokens.card};border-radius:6px;padding:8px}button:focus-visible,input:focus-visible{outline:2px solid ${tokens.ring};outline-offset:2px}</style></head><body>${html}</body></html>`,
    [html, resolvedTheme, tokens],
  );
  return (
    <iframe
      title={str(p, "title")}
      sandbox={readOnly ? "" : "allow-scripts"}
      referrerPolicy="no-referrer"
      srcDoc={srcDoc}
      className="w-full rounded-lg border border-border bg-card"
      style={{ height: num(p, "height", 360) }}
    />
  );
}

export function UiNode({ name, p }: { name: string; p: Props }) {
  const render = useRenderNode();
  const content = () => render(p.children);
  const color = tones[str(p, "tone")] ?? tones.default;
  const fill = svgTones[str(p, "tone")] ?? svgTones.default;
  switch (name) {
    case "Kpi": {
      const records = p.data as Props[];
      const value = records[0]?.[str(p, "column")];
      return (
        <section className="rounded-lg border border-border bg-card p-4">
          <p className="text-sm text-muted-foreground">{str(p, "label")}</p>
          <p className="mt-2 break-words text-2xl font-semibold tabular-nums">
            {typeof value === "number"
              ? new Intl.NumberFormat(undefined, {
                  maximumFractionDigits: num(p, "digits", 2),
                }).format(value)
              : String(value ?? "No result")}
          </p>
        </section>
      );
    }
    case "Col":
      return (
        <div
          className="flex min-w-0 flex-col"
          style={{ gap: num(p, "gap", 3) * 4 }}
        >
          {content()}
        </div>
      );
    case "Row":
      return (
        <div
          className="flex min-w-0 flex-col flex-wrap sm:flex-row sm:items-center [&>*]:min-w-0 [&>*]:flex-1"
          style={{ gap: num(p, "gap", 3) * 4 }}
        >
          {content()}
        </div>
      );
    case "Grid":
      return (
        <div
          className={cn(
            "grid min-w-0 grid-cols-1",
            num(p, "columns", 2) >= 2 && "sm:grid-cols-2",
            num(p, "columns", 2) >= 3 && "xl:grid-cols-3",
            num(p, "columns", 2) === 4 && "2xl:grid-cols-4",
          )}
          style={{ gap: num(p, "gap", 3) * 4 }}
        >
          {content()}
        </div>
      );
    case "Box":
      return (
        <section
          className={cn("min-w-0 rounded-lg bg-surface-1 p-3 sm:p-4", color)}
        >
          {content()}
        </section>
      );
    case "Card":
      return (
        <section className="min-w-0 space-y-3">
          <h3 className="mb-2 text-base font-semibold">{str(p, "title")}</h3>
          {p.description ? (
            <p className="mb-3 text-sm text-muted-foreground">
              {str(p, "description")}
            </p>
          ) : null}
          {content()}
        </section>
      );
    case "Carousel":
      return <Pages p={p} />;
    case "Popover":
      return (
        <details className="min-w-0 rounded-lg border border-border p-3">
          <summary className="min-h-11 cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">
            {str(p, "label")}
          </summary>
          <div className="mt-3">{content()}</div>
        </details>
      );
    case "Divider":
      return <hr className="border-border" />;
    case "Spacer":
      return <div aria-hidden style={{ height: num(p, "size", 2) * 4 }} />;
    case "Title":
      return (
        <h3 className={cn("break-words text-lg font-semibold", color)}>
          {str(p, "text")}
        </h3>
      );
    case "Text":
      return (
        <p className={cn("break-words whitespace-pre-wrap text-sm", color)}>
          {str(p, "text")}
        </p>
      );
    case "Caption":
      return (
        <p className="break-words text-xs text-muted-foreground">
          {str(p, "text")}
        </p>
      );
    case "Label":
      return (
        <p className={cn("text-sm font-medium", color)}>{str(p, "text")}</p>
      );
    case "Badge":
      return (
        <span
          className={cn(
            "w-fit rounded-md border border-border px-2 py-1 text-xs",
            color,
          )}
        >
          {str(p, "text")}
        </span>
      );
    case "Link":
      return (
        <a
          href={str(p, "url")}
          target="_blank"
          rel="noopener noreferrer"
          referrerPolicy="no-referrer"
          className="inline-flex min-h-11 min-w-11 items-center break-all text-sm text-primary underline focus-visible:outline-2 focus-visible:outline-ring dark:text-accent-foreground"
        >
          {str(p, "text")}
        </a>
      );
    case "List":
      return p.ordered ? (
        <ol className="list-decimal space-y-2 pl-5 text-sm">{content()}</ol>
      ) : (
        <ul className="list-disc space-y-2 pl-5 text-sm">{content()}</ul>
      );
    case "ListItem":
      return <li>{content()}</li>;
    case "Markdown":
      return <Markdown offline>{str(p, "text")}</Markdown>;
    case "Quote":
      return (
        <blockquote className="border-l-2 border-primary pl-4 text-sm">
          <p>{str(p, "text")}</p>
          {p.source ? (
            <cite className="text-xs text-muted-foreground">
              {str(p, "source")}
            </cite>
          ) : null}
        </blockquote>
      );
    case "Code":
      return (
        <pre className="max-w-full overflow-x-auto rounded-lg bg-muted p-3 text-sm">
          <code>{str(p, "code")}</code>
        </pre>
      );
    case "Math":
      return <MathFormula p={p} />;
    case "Button":
      return <ActionControl p={p} />;
    case "IconButton": {
      const ActionIcon =
        (
          { Plus, Check, Info, Play, RotateCcw, ArrowRight } as Record<
            string,
            typeof Info
          >
        )[str(p, "icon")] ?? ArrowRight;
      return (
        <ActionControl p={p}>
          <ActionIcon aria-hidden />
          <span>{str(p, "label")}</span>
        </ActionControl>
      );
    }
    case "Pressable":
      return (
        <ActionControl p={p}>
          {content()}
          <span>{str(p, "label")}</span>
        </ActionControl>
      );
    case "Input":
    case "TextArea":
    case "Select":
    case "SegmentedControl":
    case "Slider":
    case "Checkbox":
    case "RadioGroup":
    case "DatePicker":
      return <Field name={name} p={p} />;
    case "Timer":
      return <Timer p={p} />;
    case "Form":
      return (
        <FormNameContext.Provider value={str(p, "name")}>
          <form
            aria-label={str(p, "name")}
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!(e.nativeEvent as SubmitEvent).submitter)
                e.currentTarget
                  .querySelector<HTMLButtonElement>("button:not([disabled])")
                  ?.click();
            }}
          >
            {content()}
          </form>
        </FormNameContext.Provider>
      );
    case "GeoMap":
      return <Suspense fallback={<p role="status">Loading map…</p>}><GeoMap p={p} /></Suspense>;
    case "SearchImage":
    case "Image":
    case "Map":
    case "YouTube":
      return <PublicMedia name={name} p={p} />;
    case "MapMarker":
      return null;
    case "Icon": {
      const Icon =
        (
          { Plus, Check, Info, Play, RotateCcw, ArrowRight } as Record<
            string,
            typeof Info
          >
        )[str(p, "name")] ?? Info;
      return (
        <Icon
          aria-label={str(p, "label") || str(p, "name")}
          className="size-5 text-muted-foreground"
        />
      );
    }
    case "Table":
      return <DataTable p={p} />;
    case "Chart":
      return <Suspense fallback={<p role="status">Loading chart…</p>}><DataChart p={p} /></Suspense>;
    case "Flashcard":
      return <Flashcard p={p} />;
    case "Celebration":
      return (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-success-strong"
        >
          <Check aria-hidden className="size-5" />
          {str(p, "text")}
        </p>
      );
    case "Svg":
      return (
        <svg
          role="img"
          aria-label={str(p, "label")}
          viewBox={str(p, "viewBox")}
          className="h-auto max-h-96 w-full"
        >
          <title>{str(p, "label")}</title>
          {content()}
        </svg>
      );
    case "G":
      return <g transform={str(p, "transform")}>{content()}</g>;
    case "Circle":
      return (
        <circle
          cx={num(p, "cx")}
          cy={num(p, "cy")}
          r={num(p, "r")}
          fill={fill}
          stroke="var(--foreground)"
        />
      );
    case "Ellipse":
      return (
        <ellipse
          cx={num(p, "cx")}
          cy={num(p, "cy")}
          rx={num(p, "rx")}
          ry={num(p, "ry")}
          fill={fill}
          stroke="var(--foreground)"
        />
      );
    case "Rect":
      return (
        <rect
          x={num(p, "x")}
          y={num(p, "y")}
          width={num(p, "width")}
          height={num(p, "height")}
          rx={num(p, "radius", 4)}
          fill={fill}
          stroke="var(--foreground)"
        />
      );
    case "Line":
      return (
        <line
          x1={num(p, "x1")}
          y1={num(p, "y1")}
          x2={num(p, "x2")}
          y2={num(p, "y2")}
          stroke="var(--foreground)"
          strokeWidth={2}
        />
      );
    case "Path":
      return <path d={str(p, "d")} fill={fill} stroke="var(--foreground)" />;
    case "Polyline":
      return (
        <polyline
          points={str(p, "points")}
          fill="none"
          stroke="var(--foreground)"
          strokeWidth={2}
        />
      );
    case "Polygon":
      return (
        <polygon
          points={str(p, "points")}
          fill={fill}
          stroke="var(--foreground)"
        />
      );
    case "SvgText":
      return (
        <text
          x={num(p, "x")}
          y={num(p, "y")}
          fill={
            p.tone === "on-primary"
              ? "var(--primary-foreground)"
              : "var(--foreground)"
          }
          fontSize={14}
        >
          {str(p, "text")}
        </text>
      );
    case "HtmlView":
      return <HtmlView p={p} />;
    default:
      return null;
  }
}
