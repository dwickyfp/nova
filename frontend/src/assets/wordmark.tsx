import { cn } from "@/lib/utils";

/** The "nova" letters are drawn in a 76 x 28 viewBox. */
const VIEWBOX_WIDTH = 76;
const VIEWBOX_HEIGHT = 28;

type WordmarkProps = {
  /** Rendered height of the "nova" letters in pixels. Width follows. */
  size?: number;
  /**
   * Product or subtitle shown beside (or under) "nova" as a small tracked
   * label, for example "Studio" or "Enterprise Intelligence OS".
   */
  label?: string;
  /** "inline" puts the label after the letters; "stacked" puts it below. */
  layout?: "inline" | "stacked";
  className?: string;
  /** Extra classes for the label, such as a navigation-muted colour. */
  labelClassName?: string;
};

/**
 * The drawn "nova" wordmark: monoline lowercase strokes in the text colour,
 * with a red four-point spark on the orbit of the "o". The spark uses the
 * logo-only `--brand-mark` token, so it matches the phoenix in both themes.
 */
export function Wordmark({
  size = 17,
  label,
  layout = "inline",
  className,
  labelClassName,
}: WordmarkProps) {
  const name = label ? `Nova ${label}` : "Nova";
  const width = (size * VIEWBOX_WIDTH) / VIEWBOX_HEIGHT;
  return (
    <span
      data-slot="wordmark"
      className={cn(
        "inline-flex min-w-0",
        layout === "stacked"
          ? "flex-col items-start gap-1"
          : "items-center gap-2",
        className,
      )}
    >
      <svg
        role="img"
        aria-label={name}
        viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
        width={width}
        height={size}
        className="shrink-0 overflow-visible"
      >
        <g
          fill="none"
          stroke="currentColor"
          strokeWidth={3.4}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M2 24V8M2 15a6.5 6.5 0 0 1 13 0V24" />
          <circle cx={27} cy={16} r={7.6} />
          <path d="M39 8l7.5 16L54 8" />
          <circle cx={64.5} cy={16} r={7.6} />
          <path d="M72.1 8.5V24" />
        </g>
        <path
          data-slot="wordmark-spark"
          fill="var(--brand-mark)"
          d="M34.5 3.3Q34.5 7.5 38.7 7.5Q34.5 7.5 34.5 11.7Q34.5 7.5 30.3 7.5Q34.5 7.5 34.5 3.3Z"
        />
      </svg>
      {label ? (
        <span
          aria-hidden="true"
          className={cn(
            "truncate text-xs leading-none font-semibold tracking-[0.12em] text-muted-foreground uppercase",
            labelClassName,
          )}
        >
          {label}
        </span>
      ) : null}
    </span>
  );
}
