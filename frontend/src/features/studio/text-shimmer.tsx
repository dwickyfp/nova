import { cn } from "@/lib/utils";

/**
 * A text label with an animated sheen, used for a live "thinking" state.
 *
 * Implemented with a CSS background animation rather than a motion library, so
 * Nova takes no new dependency for one effect. It honours reduced-motion by
 * falling back to a static muted colour.
 */
export function TextShimmer({
  children,
  className,
}: {
  children: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "nova-shimmer inline-block bg-clip-text text-sm text-transparent motion-reduce:animate-none motion-reduce:bg-none motion-reduce:text-muted-foreground",
        className,
      )}
    >
      {children}
    </span>
  );
}
