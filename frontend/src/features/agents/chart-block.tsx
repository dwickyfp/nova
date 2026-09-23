import { useEffect, useRef, useState } from "react";
import type { VisualizationSpec } from "vega-embed";
import { readToken } from "@/lib/read-token";

/**
 * Renders one Vega-Lite v5 chart from the `chart` SSE event.
 *
 * The spec is authored by the backend (sanitised there: marks whitelisted,
 * `expr`/`signal`/`href`/`url` stripped). This component renders it **with
 * `actions: false`** so the built-in Vega menu (which can export data and open
 * links) is not offered. The backend sanitisation is the primary control; this
 * is defence in depth.
 *
 * The chart follows the app's theme rather than forcing dark: a dark chart on a
 * light card is unreadable, and the agent has no idea which theme the reader is
 * in. Fonts and view sizing are set here too, because a spec authored for one
 * screenshot rarely has a readable default at conversation width.
 *
 * Vega is imported lazily so the ~1 MB runtime is only loaded when an agent
 * actually returns a chart, not for every chat answer.
 */
export function ChartBlock({ spec, fillHeight = false }: { spec: string; fillHeight?: boolean }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [failure, setFailure] = useState<{
    spec: string;
    message: string;
  } | null>(null);
  const dark = useIsDark();
  const error = failure?.spec === spec ? failure.message : null;

  useEffect(() => {
    let cancelled = false;
    type ResizableView = {
      finalize: () => void;
      width: (width: number) => ResizableView;
      height: (height: number) => ResizableView;
      resize: () => { runAsync: () => Promise<unknown> };
    };
    let view: ResizableView | null = null;
    let observer: ResizeObserver | null = null;
    let resizeFrame = 0;

    const parsed = safeParse(spec);
    if (!parsed) {
      setFailure({ spec, message: "The chart could not be read." });
      return;
    }
    const responsiveWidth = fillHeight || parsed.width == null || parsed.width === "container";

    void (async () => {
      try {
        const { default: embed } = await import("vega-embed");
        if (cancelled || !containerRef.current) return;
        const result = await embed(
          containerRef.current,
          prepareSpec(parsed, dark, fillHeight) as VisualizationSpec,
          {
            actions: false,
            renderer: "svg",
            theme: dark ? "dark" : undefined,
          },
        );
        if (cancelled) {
          result.view.finalize();
          return;
        }
        view = result.view as ResizableView;
        setFailure(null);
        if ((responsiveWidth || fillHeight) && typeof ResizeObserver !== "undefined") {
          observer = new ResizeObserver(() => {
            if (resizeFrame) cancelAnimationFrame(resizeFrame);
            resizeFrame = requestAnimationFrame(() => {
              resizeFrame = 0;
              const width = containerRef.current?.clientWidth ?? 0;
              const height = containerRef.current?.clientHeight ?? 0;
              if (!cancelled && width > 0 && (!fillHeight || height > 0) && view) {
                const resizing = responsiveWidth ? view.width(width) : view;
                void (fillHeight ? resizing.height(height) : resizing)
                  .resize()
                  .runAsync()
                  .catch(() => {
                    if (!cancelled) {
                      setFailure({
                        spec,
                        message: "The chart could not be rendered.",
                      });
                    }
                  });
              }
            });
          });
          observer.observe(containerRef.current);
        }
      } catch {
        if (!cancelled) {
          setFailure({ spec, message: "The chart could not be rendered." });
        }
      }
    })();

    return () => {
      cancelled = true;
      observer?.disconnect();
      if (resizeFrame) cancelAnimationFrame(resizeFrame);
      try {
        view?.finalize();
      } catch {
        // A view that never mounted has nothing to finalize.
      }
    };
  }, [spec, dark, fillHeight]);

  return (
    <>
      <div
        ref={containerRef}
        data-testid="vega-chart-container"
        className={fillHeight ? "h-full w-full min-w-0 overflow-hidden" : "w-full min-w-0 overflow-x-auto"}
        hidden={Boolean(error)}
      />
      {error ? (
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          {error}
        </div>
      ) : null}
    </>
  );
}

/**
 * Watch the document's theme, so a chart re-renders when the user switches.
 * Vega applies its theme at embed time, so a stale chart would keep the wrong
 * palette until it unmounted.
 */
function useIsDark(): boolean {
  const [dark, setDark] = useState(
    () =>
      typeof document !== "undefined" &&
      document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    const root = document.documentElement;
    const sync = () => setDark(root.classList.contains("dark"));
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

/**
 * Give the spec the sizing and type a conversation card needs.
 *
 * The model authors a chart to answer a question, not to fit a 640px pane: it
 * omits `width`, leaves labels at Vega's default 11px, and lets a long category
 * axis crowd. These are presentation defaults applied here, so every chart in
 * the transcript reads the same way. Colors come from the app's own tokens in
 * the mode currently on screen, so the chart belongs to the surrounding card
 * instead of importing a palette of its own. Anything the model set explicitly
 * wins.
 */
function prepareSpec(
  parsed: Record<string, unknown>,
  dark: boolean,
  fillHeight = false,
): Record<string, unknown> {
  // Read the live token values rather than hardcoding a palette: the theme owns
  // the colors, and this component only borrows them. The fallback is only
  // reached if the token is missing, so it mirrors the theme's own value for
  // the mode currently on screen.
  const axis = dark
    ? {
        labelColor: readToken("--muted-foreground", "#a1a1aa"),
        titleColor: readToken("--foreground", "#fafafa"),
        domainColor: readToken("--border", "#3f3f46"),
        gridColor: readToken("--border", "#3f3f46"),
        gridOpacity: 0.5,
      }
    : {
        labelColor: readToken("--muted-foreground", "#71717a"),
        titleColor: readToken("--foreground", "#18181b"),
        domainColor: readToken("--border", "#e4e4e7"),
        gridColor: readToken("--border", "#e4e4e7"),
        gridOpacity: 0.5,
      };

  const config = {
    ...(parsed.config as Record<string, unknown> | undefined),
    background: "transparent",
    font: "inherit",
    axis: {
      ...axis,
      labelFontSize: 11,
      titleFontSize: 11,
      titleFontWeight: "normal" as const,
      labelLimit: 160,
      tickSize: 4,
    },
    legend: { labelFontSize: 11, titleFontSize: 11 },
    view: { stroke: "transparent" },
  };

  const prepared: Record<string, unknown> = {
    ...parsed,
    config,
    // Fill the card instead of Vega's narrow default, and leave room for
    // horizontal category labels a bar chart needs.
    width: fillHeight ? "container" : (parsed.width ?? "container"),
    height: fillHeight ? "container" : (parsed.height ?? 260),
    autosize: parsed.autosize ?? { type: "fit", contains: "padding" },
    padding: parsed.padding ?? { left: 4, top: 8, right: 8, bottom: 4 },
  };
  return prepared;
}

function safeParse(spec: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(spec);
    return typeof parsed === "object" &&
      parsed !== null &&
      !Array.isArray(parsed)
      ? parsed
      : null;
  } catch {
    return null;
  }
}
