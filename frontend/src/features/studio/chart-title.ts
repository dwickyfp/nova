/**
 * Read a Vega-Lite spec's title for the card header, and return the spec with
 * the title removed so the chart itself does not print it a second time. A
 * spec that cannot be parsed is passed through untouched.
 */
export function splitChartTitle(spec: string): {
  title?: string;
  body: string;
} {
  try {
    const parsed = JSON.parse(spec) as Record<string, unknown>;
    const raw = parsed.title;
    const title = typeof raw === "string" && raw.trim() ? raw : undefined;
    if (!title) return { body: spec };
    delete parsed.title;
    return { title, body: JSON.stringify(parsed) };
  } catch {
    return { body: spec };
  }
}
