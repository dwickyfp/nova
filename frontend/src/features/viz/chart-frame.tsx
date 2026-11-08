import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { ResponsiveContainer } from "recharts";
import { cn } from "@/lib/utils";

type Size = { width: number; height: number };

function measure(element: HTMLElement): Size {
  const box = element.getBoundingClientRect();
  return { width: Math.round(box.width), height: Math.round(box.height) };
}

/**
 * A chart box that draws on its first paint and stays drawn.
 *
 * Recharts' percentage `ResponsiveContainer` measures its box in an effect,
 * after the browser has painted, so every chart first paints empty. It also
 * unmounts the chart whenever the box reads 0 wide, which happens for a frame
 * while the transcript reflows. Both read as a chart that disappears and comes
 * back. This frame measures before paint, keeps the last real size through a
 * transient 0, and hands Recharts fixed dimensions, so it never re-measures or
 * remounts on its own.
 */
export function ChartFrame({
  className,
  style,
  children,
}: {
  /** Sizes the box; the chart fills it. Give it a height (`h-64`, `h-full`). */
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<Size | null>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const apply = (next: Size) => {
      // A collapsed box (hidden tab, reflow) keeps the last drawn chart.
      if (next.width <= 0 || next.height <= 0) return;
      setSize((current) =>
        current && current.width === next.width && current.height === next.height
          ? current
          : next,
      );
    };
    apply(measure(element));
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => apply(measure(element)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={ref} className={cn("min-w-0", className)} style={style}>
      {size ? (
        <ResponsiveContainer width={size.width} height={size.height}>
          {children}
        </ResponsiveContainer>
      ) : null}
    </div>
  );
}
