/**
 * One headline figure: the shared tile of registry `KpiTiles` and page
 * `Metric`s. A change reads as a coloured sublabel (+12.4%) and a badge as a
 * quiet chip, so a row of tiles scans like a dashboard header.
 */
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils";

export type Figure = {
  label: string;
  value: number | null;
  format?: "number" | "currency" | "percent";
  /** Relative change as a fraction: 0.12 reads as +12%. */
  delta?: number | null;
  badge?: string;
  currency?: string;
  decimals?: number;
};

function formatter(figure: Figure): Intl.NumberFormat {
  const options: Intl.NumberFormatOptions = {
    maximumFractionDigits: figure.decimals ?? 2,
    minimumFractionDigits: figure.decimals !== undefined ? Math.min(figure.decimals, 2) : 0,
  };
  if (figure.format === "currency" && figure.currency) {
    Object.assign(options, { style: "currency", currency: figure.currency });
  }
  if (figure.format === "percent") options.style = "percent";
  return new Intl.NumberFormat(undefined, options);
}

/** The exact value, as shown in the tile's title. */
export function exactFigure(figure: Figure): string {
  return figure.value === null ? "" : formatter(figure).format(figure.value);
}

/** Large figures read compact on a tile; the exact value stays in its title. */
export function compactFigure(figure: Figure): string {
  if (figure.value === null) return "";
  if (Math.abs(figure.value) < 1_000_000 || figure.format === "percent") return exactFigure(figure);
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
    ...(figure.format === "currency" && figure.currency
      ? { style: "currency", currency: figure.currency }
      : {}),
  }).format(figure.value);
}

const change = new Intl.NumberFormat(undefined, {
  style: "percent",
  maximumFractionDigits: 1,
  signDisplay: "exceptZero",
});

export function FigureTile({ figure }: { figure: Figure }) {
  const delta = typeof figure.delta === "number" && Number.isFinite(figure.delta) ? figure.delta : null;
  return (
    <div className="min-w-0 rounded-xl border bg-card px-4 py-3" data-figure>
      <dt className="truncate text-sm text-muted-foreground">{figure.label}</dt>
      <dd
        className="mt-1 truncate text-2xl font-semibold tabular-nums"
        title={figure.value === null ? undefined : exactFigure(figure)}
      >
        {figure.value === null ? (
          <span className="text-muted-foreground">No value</span>
        ) : (
          compactFigure(figure)
        )}
      </dd>
      {delta !== null || figure.badge ? (
        <dd className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          {delta !== null ? (
            <span
              className={cn(
                "inline-flex items-center gap-0.5 font-medium tabular-nums",
                delta > 0 ? "text-success-strong" : delta < 0 ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {delta > 0 ? (
                <ArrowUpRight className="size-3.5" aria-hidden="true" />
              ) : delta < 0 ? (
                <ArrowDownRight className="size-3.5" aria-hidden="true" />
              ) : null}
              {change.format(delta)}
            </span>
          ) : null}
          {figure.badge ? (
            <span className="truncate rounded-full bg-muted px-2 py-0.5 text-muted-foreground">
              {figure.badge}
            </span>
          ) : null}
        </dd>
      ) : null}
    </div>
  );
}
