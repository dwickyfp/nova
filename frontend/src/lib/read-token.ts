/**
 * Recharts takes colours as concrete values, not Tailwind classes, so a chart
 * needs the resolved token value at runtime. Reading it off the document keeps
 * the chart on the same palette as the rest of the UI, including dark mode.
 *
 * The fallback is required: getComputedStyle does not exist during SSR. It is
 * the only sanctioned place for a hex literal outside theme.css, enforced by
 * the design-system gate in DESIGN.md section 3 A2.
 */
export function readToken(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim()
  return value || fallback
}
