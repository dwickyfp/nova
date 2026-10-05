import { useEffect, useRef, useState } from "react";
import type { EChartsCoreOption } from "echarts/core";
import { readToken } from "@/lib/read-token";

export type ChartTokens = {
  series: string;
  text: string;
  muted: string;
  border: string;
  surface: string;
};

function tokens(dark: boolean): ChartTokens {
  // The theme owns the colors. Each fallback is only reached when a token is
  // missing and mirrors the theme's value for the mode on screen.
  return dark
    ? {
        series: readToken("--chart-4", "#7398ed"),
        text: readToken("--foreground", "#f7f8fa"),
        muted: readToken("--muted-foreground", "#9ca8b8"),
        border: readToken("--border", "#28313d"),
        surface: readToken("--background", "#0b0f14"),
      }
    : {
        series: readToken("--chart-4", "#4f7ee8"),
        text: readToken("--foreground", "#18181b"),
        muted: readToken("--muted-foreground", "#71717a"),
        border: readToken("--border", "#e4e4e7"),
        surface: readToken("--background", "#ffffff"),
      };
}

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
 * One Apache ECharts instance that follows the app theme and its container.
 *
 * The runtime is imported lazily and by module, so a reader who never opens
 * News never downloads it and only the chart types News draws are bundled.
 */
export function EChart({
  build,
  height,
  label,
}: {
  build: (tokens: ChartTokens) => EChartsCoreOption;
  height: number;
  label: string;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);
  const dark = useIsDark();

  useEffect(() => {
    let cancelled = false;
    let dispose = () => {};
    void (async () => {
      try {
        const [core, charts, components, renderers] = await Promise.all([
          import("echarts/core"),
          import("echarts/charts"),
          import("echarts/components"),
          import("echarts/renderers"),
        ]);
        if (cancelled || !container.current) return;
        core.use([
          charts.LineChart,
          charts.BarChart,
          components.GridComponent,
          components.TooltipComponent,
          components.MarkLineComponent,
          renderers.SVGRenderer,
        ]);
        const chart = core.init(container.current, undefined, { renderer: "svg" });
        chart.setOption(build(tokens(dark)));
        const observer =
          typeof ResizeObserver === "undefined"
            ? null
            : new ResizeObserver(() => chart.resize());
        const node = container.current;
        observer?.observe(node);
        // A click that opens a panel over the chart never sends a mouse-out, so
        // the tooltip would stay on top of it.
        const hide = () => chart.dispatchAction({ type: "hideTip" });
        node.addEventListener("pointerleave", hide);
        node.addEventListener("pointerdown", hide);
        dispose = () => {
          observer?.disconnect();
          node.removeEventListener("pointerleave", hide);
          node.removeEventListener("pointerdown", hide);
          chart.dispose();
        };
        setFailed(false);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      dispose();
    };
  }, [build, dark]);

  if (failed)
    return (
      <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
        The chart could not be rendered.
      </p>
    );
  return (
    <div
      ref={container}
      role="img"
      aria-label={label}
      data-testid="news-chart"
      className="w-full min-w-0"
      style={{ height }}
    />
  );
}
