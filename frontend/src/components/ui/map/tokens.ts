/** Convert Nova CSS colors (including oklch) to a MapLibre-compatible color. */
export function mapColor(name: string): string {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  const context = document
    .createElement("canvas")
    .getContext("2d", { willReadFrequently: true });
  if (!context) return value;
  context.fillStyle = value;
  context.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
  return `rgba(${r},${g},${b},${a / 255})`;
}

export function motionDuration(milliseconds: number): number {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ? 0
    : milliseconds;
}
