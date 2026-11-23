/**
 * How a result value reads in Studio.
 *
 * The engine returns DECIMAL values as strings (`24999000.00000000`) and
 * DATETIME values without an offset (`2025-06-01T00:00:00`). Shown as they
 * arrive they read as machine output. This is display only: sorting, filtering
 * and CSV export keep the value the engine returned.
 */

/** Columns whose numbers are labels, not amounts: never grouped or rounded. */
const IDENTIFIER = /(^|_)(id|code|key|year|zip|postcode|phone|sku|number)$/i;
const NUMERIC = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/;
/** A wall-clock date or datetime with no zone: shown as written, never shifted. */
const NAIVE_DATE =
  /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?$/;

const dateOnly = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});
const dateTime = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

/** `145886000` → `145,886,000`; `10165333.3333` → `10,165,333.33`; `0.12345` → `0.1235`. */
export function formatAmount(value: number): string {
  const digits = Number.isInteger(value) ? 0 : Math.abs(value) < 1 ? 4 : 2;
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);
}

/** The value as a reader sees it in a result cell or tile. */
export function formatResultValue(
  value: string | number | boolean | null | undefined,
  column = "",
): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean" || IDENTIFIER.test(column)) return String(value);
  if (typeof value === "number") {
    return Number.isFinite(value) ? formatAmount(value) : String(value);
  }
  const text = value.trim();
  if (NUMERIC.test(text)) {
    const whole = text.replace(/^[+-]/, "").split(".")[0];
    // A leading zero is a code ("00123"); more than 15 digits would lose
    // precision as a JavaScript number. Both stay exactly as returned.
    if ((whole.length > 1 && whole.startsWith("0")) || whole.length > 15) return value;
    return formatAmount(Number(text));
  }
  const date = NAIVE_DATE.exec(text);
  if (date) {
    const [, year, month, day, hour = "0", minute = "0", second = "0"] = date;
    const instant = Date.UTC(+year, +month - 1, +day, +hour, +minute, +second);
    const parsed = new Date(instant);
    if (Number.isNaN(instant) || parsed.getUTCDate() !== +day) return value;
    const midnight = +hour === 0 && +minute === 0 && +second === 0;
    return (midnight ? dateOnly : dateTime).format(parsed);
  }
  return value;
}

/** `shipping_city` → `Shipping city`, `revenue` → `Revenue`; any other name is kept. */
export function columnLabel(column: string): string {
  if (!/^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/.test(column)) return column;
  const words = column
    .split("_")
    .map((word) => (word === "id" ? "ID" : word))
    .join(" ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
