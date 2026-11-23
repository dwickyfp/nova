/**
 * A page-like answer: the layout program (catalog `nova-layout-1`) drawn with
 * Nova's own components as it streams.
 *
 * The server checks every statement before it is sent, so the program holds
 * only verified figures, native `DatavizChart`s and `ResultTable` rows. The
 * OpenUI streaming parser places each statement as soon as it arrives; a
 * statement that fails here is left out and the rest of the page still shows.
 * Controls are local: they recompute values already on the page, start from
 * the program's defaults on every load and never send anything.
 */
import { createContext, lazy, Suspense, useCallback, useContext, useId, useMemo, useState, type ReactNode } from "react";
import {
  createLibrary,
  defineComponent,
  Renderer,
  useIsStreaming,
  useStateField,
} from "@openuidev/react-lang";
import type { ParseResult } from "@openuidev/react-lang";
import { Markdown } from "@/features/assistant/markdown";
import { cn } from "@/lib/utils";
import type { DatavizLocator } from "../../dataviz/api";
import { ResultTable } from "../../result-table";
import { LAYOUT_COMPONENTS, LAYOUT_ROOT, type LayoutComponentName } from "./layout";
import { DatavizChart, GeoMap as RegistryGeoMap } from "./registry";
import type { GeoMapProps } from "./schemas";
import type { PlaceMapProps } from "../../intelligent-ui/place-map";
import { cellOf } from "./spec-chart";
import type { LayoutBlock } from "./block";
import { FIGURE_GRID, FigureTile, type Figure } from "./figure-tile";

type PageContext = {
  /** The turn is stored, so draft charts can read their governed rows. */
  ready: boolean;
  /** Where the page is stored, for Add to Dataviz. */
  dataviz: Omit<DatavizLocator, "node"> | null;
};

const PageRuntime = createContext<PageContext>({ ready: false, dataviz: null });

type Props = Record<string, unknown>;
const text = (value: unknown) => (typeof value === "string" ? value : "");
const list = (value: unknown) => (Array.isArray(value) ? value : []);
const number = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/** The chart's position on its page, read from its signed draft reference. */
export function chartPosition(ref: unknown): number | null {
  if (typeof ref !== "string") return null;
  const [, payload] = ref.split(".");
  if (!payload) return null;
  try {
    const padded = payload.replace(/-/g, "+").replace(/_/g, "/");
    const value = JSON.parse(atob(padded + "=".repeat((4 - (padded.length % 4)) % 4))) as {
      n?: unknown;
    };
    return typeof value.n === "number" && Number.isInteger(value.n) ? value.n : null;
  } catch {
    return null;
  }
}

function Page({ props, renderNode }: { props: Props; renderNode: (value: unknown) => ReactNode }) {
  const streaming = useIsStreaming();
  return (
    <article className="nova-layout min-w-0 space-y-6" data-component="Page" aria-busy={streaming}>
      {renderNode(props.children)}
      {streaming ? (
        <div
          role="status"
          aria-label="Composing the page"
          className="h-20 rounded-xl bg-muted/50 motion-safe:animate-pulse"
        />
      ) : null}
    </article>
  );
}

function Header({ props }: { props: Props }) {
  return (
    <header className="min-w-0 space-y-1" data-component="Header">
      <h2 className="text-xl font-semibold leading-tight tracking-tight text-balance">
        {text(props.title)}
      </h2>
      {text(props.subtitle) ? (
        <p className="text-sm text-muted-foreground text-pretty">{text(props.subtitle)}</p>
      ) : null}
    </header>
  );
}

function Section({ props, renderNode }: { props: Props; renderNode: (value: unknown) => ReactNode }) {
  const id = useId();
  const title = text(props.title);
  return (
    <section
      className="min-w-0 space-y-3"
      aria-labelledby={title ? id : undefined}
      data-component="Section"
    >
      {title ? (
        <h3 id={id} className="text-base font-semibold">
          {title}
        </h3>
      ) : null}
      <div className="min-w-0 space-y-4">{renderNode(props.children)}</div>
      {text(props.note) ? <p className="text-xs text-muted-foreground">{text(props.note)}</p> : null}
    </section>
  );
}

function Row({ props, renderNode }: { props: Props; renderNode: (value: unknown) => ReactNode }) {
  const count = list(props.children).filter(Boolean).length;
  return (
    <div
      className={cn(
        "grid min-w-0 grid-cols-1 gap-4",
        count >= 3 ? "lg:grid-cols-3" : count === 2 ? "lg:grid-cols-2" : "",
      )}
      data-component="Row"
    >
      {renderNode(props.children)}
    </div>
  );
}

function KpiTiles({ props, renderNode }: { props: Props; renderNode: (value: unknown) => ReactNode }) {
  const title = text(props.title);
  // Tiles the parser left out (an invalid Metric) leave no empty row behind.
  if (!list(props.items).some(Boolean)) return null;
  return (
    <section className="min-w-0" aria-label={title || "Key figures"} data-component="KpiTiles">
      {title ? <h3 className="mb-2 text-sm font-medium">{title}</h3> : null}
      <dl className={FIGURE_GRID}>
        {renderNode(props.items)}
      </dl>
    </section>
  );
}

function Metric({ props }: { props: Props }) {
  const format = text(props.format);
  const figure: Figure = {
    label: text(props.label),
    value: number(props.value),
    format: format === "currency" || format === "percent" ? format : "number",
    delta: number(props.delta),
    badge: text(props.badge) || undefined,
    currency: text(props.currency) || undefined,
    decimals: number(props.decimals) ?? undefined,
  };
  return <FigureTile figure={figure} />;
}

function Chart({ props }: { props: Props }) {
  const { ready, dataviz } = useContext(PageRuntime);
  const position = chartPosition(props.draft_ref);
  const locator =
    dataviz && position !== null
      ? { ...dataviz, node: { component: "DatavizChart" as const, position } }
      : null;
  return (
    <DatavizChart
      props={{
        title: text(props.title),
        chart: (props.chart ?? {}) as Record<string, unknown>,
        draft_ref: text(props.draft_ref),
      }}
      ready={ready}
      dataviz={locator}
    />
  );
}

function Table({ props }: { props: Props }) {
  const columns = list(props.columns).map(String);
  return (
    <div className="min-w-0" data-component="ResultTable">
      <ResultTable
        block={{
          title: text(props.title) || "Result",
          columns,
          rows: list(props.rows).map((row) => list(row).map(cellOf)),
        }}
        note={null}
      />
    </div>
  );
}

const PlaceMapView = lazy(() => import("../../intelligent-ui/place-map"));

/** A map of verified rows: the shared GeoMap, its rows read by the server. */
function GeoMap({ props }: { props: Props }) {
  const map = props.map as GeoMapProps | undefined;
  return map ? <RegistryGeoMap props={map} /> : null;
}

/** Public places and road routes, fetched and checked when the page was written. */
function PlaceMap({ props }: { props: Props }) {
  return (
    <div className="nova-chat-item min-w-0">
      <Suspense fallback={<p role="status" className="text-sm text-muted-foreground">Loading map…</p>}>
        <PlaceMapView {...(props as unknown as PlaceMapProps)} />
      </Suspense>
    </div>
  );
}

/** A public image from Wikimedia Commons, credited to its page there. */
function Picture({ props }: { props: Props }) {
  const [failed, setFailed] = useState(false);
  const url = text(props.url);
  const source = text(props.source);
  const alt = text(props.alt);
  if (!url.startsWith("https://upload.wikimedia.org/")) return null;
  return (
    <figure className="min-w-0 space-y-1.5" data-component="Picture">
      {failed ? (
        <p className="rounded-lg border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">{alt}</p>
      ) : (
        <img
          src={url}
          alt={alt}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
          className="max-h-96 w-full rounded-lg bg-muted object-cover"
        />
      )}
      <figcaption className="text-xs text-muted-foreground">
        {alt} ·{" "}
        <a
          href={source.startsWith("https://commons.wikimedia.org/") ? source : "https://commons.wikimedia.org/"}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className="underline-offset-2 hover:underline"
        >
          Wikimedia Commons
        </a>
      </figcaption>
    </figure>
  );
}

function Text({ props }: { props: Props }) {
  return (
    <div className="min-w-0 text-sm leading-relaxed" data-component="Text">
      <Markdown offline>{text(props.markdown)}</Markdown>
    </div>
  );
}

function Controls({ props, renderNode }: { props: Props; renderNode: (value: unknown) => ReactNode }) {
  const id = useId();
  return (
    <section
      className="min-w-0 rounded-xl border bg-muted/30 p-4"
      aria-labelledby={id}
      data-component="Controls"
    >
      <h3 id={id} className="text-sm font-semibold">
        {text(props.title)}
      </h3>
      <div
        className={cn(
          "mt-3 grid min-w-0 grid-cols-1 gap-4",
          list(props.children).filter(Boolean).length > 1 && "sm:grid-cols-2",
        )}
      >
        {renderNode(props.children)}
      </div>
      {text(props.note) ? (
        <p className="mt-3 text-xs text-muted-foreground">{text(props.note)}</p>
      ) : null}
    </section>
  );
}

function sliderText(value: number, format: string): string {
  const formatted = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
  return format === "percent" ? `${value > 0 ? "+" : ""}${formatted}%` : formatted;
}

function Slider({ props }: { props: Props }) {
  const id = useId();
  const field = useStateField<number>("value", props.value as number);
  const streaming = useIsStreaming();
  const min = number(props.min) ?? 0;
  const max = number(props.max) ?? 100;
  const step = number(props.step) ?? 1;
  const value = number(field.value) ?? min;
  const format = text(props.format);
  return (
    <div className="min-w-0" data-component="Slider">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-sm">
          {text(props.label)}
        </label>
        <output htmlFor={id} className="text-sm font-semibold tabular-nums">
          {sliderText(value, format)}
        </output>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={streaming}
        aria-valuetext={sliderText(value, format)}
        onChange={(event) => field.setValue(Number(event.target.value))}
        className="mt-2 h-11 w-full cursor-pointer accent-primary disabled:cursor-default"
      />
      <div className="flex justify-between text-xs text-muted-foreground tabular-nums" aria-hidden="true">
        <span>{sliderText(min, format)}</span>
        <span>{sliderText(max, format)}</span>
      </div>
    </div>
  );
}

type Option = { label: string; value: string | number };

function options(value: unknown): Option[] {
  return list(value).flatMap((item): Option[] => {
    if (typeof item === "string") return [{ label: item, value: item }];
    if (item && typeof item === "object") {
      const { label, value: v } = item as { label?: unknown; value?: unknown };
      if (typeof label === "string" && (typeof v === "string" || typeof v === "number"))
        return [{ label, value: v }];
    }
    return [];
  });
}

function Segmented({ props }: { props: Props }) {
  const id = useId();
  const field = useStateField("value", props.value);
  const streaming = useIsStreaming();
  const choices = options(props.options);
  return (
    <div className="min-w-0" data-component="Segmented">
      <p id={id} className="text-sm">
        {text(props.label)}
      </p>
      <div
        role="radiogroup"
        aria-labelledby={id}
        className="mt-2 inline-flex max-w-full flex-wrap gap-1 rounded-lg border bg-background p-1"
      >
        {choices.map((choice) => {
          const selected = field.value === choice.value;
          return (
            <button
              key={String(choice.value)}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={streaming}
              onClick={() => field.setValue(choice.value)}
              className={cn(
                "min-h-9 rounded-md px-3 text-sm focus-visible:outline-2 focus-visible:outline-ring",
                selected ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {choice.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Toggle({ props }: { props: Props }) {
  const id = useId();
  const field = useStateField<boolean>("value", props.value as boolean);
  const streaming = useIsStreaming();
  const on = field.value === true;
  return (
    <div className="flex min-h-11 min-w-0 items-center justify-between gap-3" data-component="Toggle">
      <label htmlFor={id} className="text-sm">
        {text(props.label)}
      </label>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={on}
        disabled={streaming}
        onClick={() => field.setValue(!on)}
        className={cn(
          "relative h-6 w-11 shrink-0 rounded-full border transition-colors focus-visible:outline-2 focus-visible:outline-ring",
          on ? "bg-primary" : "bg-muted",
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "absolute top-0.5 size-4.5 rounded-full bg-background shadow transition-transform",
            on ? "translate-x-5.5" : "translate-x-0.5",
          )}
        />
      </button>
    </div>
  );
}

type Renderer = (args: { props: Props; renderNode: (value: unknown) => ReactNode }) => ReactNode;

/** Browser adapters; components only the model writes are rewritten by the server. */
const VIEWS: Partial<Record<LayoutComponentName, Renderer>> = {
  Page,
  Header,
  Section,
  Row,
  KpiTiles,
  Metric,
  DatavizChart: Chart,
  ResultTable: Table,
  Text,
  GeoMap,
  PlaceMap,
  Picture,
  Controls,
  Slider,
  Segmented,
  Toggle,
};

const library = createLibrary({
  root: LAYOUT_ROOT,
  components: Object.entries(VIEWS).map(([name, View]) =>
    defineComponent({
      name,
      description: LAYOUT_COMPONENTS[name as LayoutComponentName].description,
      props: LAYOUT_COMPONENTS[name as LayoutComponentName].props,
      component: ({ props, renderNode }) => (
        <View props={props as Props} renderNode={renderNode as (value: unknown) => ReactNode} />
      ),
    }),
  ),
});

export function LayoutView({
  block,
  program,
  streaming,
  ready,
  dataviz = null,
}: {
  block: LayoutBlock;
  program: string;
  /** The program is still arriving. */
  streaming: boolean;
  ready: boolean;
  dataviz?: Omit<DatavizLocator, "node"> | null;
}) {
  const [empty, setEmpty] = useState(false);
  const onParseResult = useCallback((result: ParseResult | null) => {
    setEmpty(!result?.root);
  }, []);
  const runtime = useMemo(() => ({ ready, dataviz }), [ready, dataviz]);
  if (!streaming && (!program.trim() || empty) && block.fallback) {
    return (
      <div className="text-sm" data-component="LayoutFallback">
        <Markdown offline>{block.fallback}</Markdown>
      </div>
    );
  }
  return (
    <PageRuntime.Provider value={runtime}>
      <div className="nova-chat-item min-w-0" data-catalog={block.catalog_version}>
        <Renderer
          response={program}
          library={library}
          isStreaming={streaming}
          initialState={block.state_defaults}
          onParseResult={onParseResult}
          toolProvider={null}
          publishObservability={false}
        />
        {block.provenance?.legacy ? (
          <p className="mt-3 text-xs text-muted-foreground">
            An earlier interactive answer, shown read-only.
          </p>
        ) : null}
      </div>
    </PageRuntime.Provider>
  );
}
