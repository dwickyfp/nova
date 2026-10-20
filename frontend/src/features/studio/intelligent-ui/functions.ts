import { defineFunction } from "@openuidev/lang-core";
import { z } from "zod";

const value = z.unknown();
const list = z.array(value).max(1000);
const number = z.number().finite();
const text = z.string().max(16000);
const count = z.number().int().min(0).max(1000);
const records = z.array(z.record(z.string(), value)).max(1000);
type Row = Record<string, unknown>;
const decimal = /^-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
const numeric = (v: unknown) => {
  const n = typeof v === "string" && decimal.test(v) ? Number(v) : v;
  if (typeof n !== "number") return 0;
  if (!Number.isFinite(n) || (Number.isInteger(n) && !Number.isSafeInteger(n)))
    throw new Error(
      "Local calculation exceeds numeric precision. Ask Nova to calculate it.",
    );
  if (n === 0 && typeof v === "string" && /[1-9]/.test(v.split(/e/i)[0]))
    throw new Error(
      "Local calculation exceeds numeric precision. Ask Nova to calculate it.",
    );
  return n;
};
const same = (a: unknown, b: unknown) =>
  JSON.stringify(a) === JSON.stringify(b);
const date = (v: string) => new Date(v);
const iso = (v: Date) =>
  Number.isFinite(v.getTime()) ? v.toISOString().slice(0, 10) : null;
const stats = (values: unknown[]) => {
  const ns = values
    .filter(
      (v) =>
        (typeof v === "number" && Number.isFinite(v)) ||
        (typeof v === "string" && decimal.test(v)),
    )
    .map(numeric)
    .sort((a, b) => a - b);
  const sum = ns.reduce((a, b) => a + b, 0);
  return {
    count: ns.length,
    sum,
    mean: ns.length ? sum / ns.length : null,
    min: ns[0] ?? null,
    max: ns[ns.length - 1] ?? null,
    median: ns.length
      ? (ns[Math.floor((ns.length - 1) / 2)] + ns[Math.floor(ns.length / 2)]) /
        2
      : null,
  };
};

export const uiFunctions = [
  defineFunction({
    name: "Append",
    description: "Append an item to a list.",
    params: z.object({ list, item: value }),
    fn: (p) => [...p.list, p.item].slice(0, 1000),
  }),
  defineFunction({
    name: "Prepend",
    description: "Prepend an item.",
    params: z.object({ list, item: value }),
    fn: (p) => [p.item, ...p.list].slice(0, 1000),
  }),
  defineFunction({
    name: "Concat",
    description: "Combine two lists.",
    params: z.object({ first: list, second: list }),
    fn: (p) => [...p.first, ...p.second].slice(0, 1000),
  }),
  defineFunction({
    name: "Remove",
    description: "Remove matching items.",
    params: z.object({ list, item: value }),
    fn: (p) => p.list.filter((v) => !same(v, p.item)),
  }),
  defineFunction({
    name: "RemoveAt",
    description: "Remove at an index.",
    params: z.object({ list, index: count }),
    fn: (p) => p.list.filter((_, i) => i !== p.index),
  }),
  defineFunction({
    name: "Update",
    description: "Replace at an index.",
    params: z.object({ list, index: count, item: value }),
    fn: (p) => p.list.map((v, i) => (i === p.index ? p.item : v)),
  }),
  defineFunction({
    name: "Toggle",
    description: "Toggle membership.",
    params: z.object({ list, item: value }),
    fn: (p) =>
      p.list.some((v) => same(v, p.item))
        ? p.list.filter((v) => !same(v, p.item))
        : [...p.list, p.item].slice(0, 1000),
  }),
  defineFunction({
    name: "Includes",
    description: "Test membership.",
    params: z.object({ list, item: value }),
    fn: (p) => p.list.some((v) => same(v, p.item)),
  }),
  defineFunction({
    name: "Find",
    description: "Find a record by field.",
    params: z.object({ rows: records, field: text, value }),
    fn: (p) => p.rows.find((r) => same(r[p.field], p.value)) ?? null,
  }),
  defineFunction({
    name: "IndexOf",
    description: "Find an item index.",
    params: z.object({ list, item: value }),
    fn: (p) => p.list.findIndex((v) => same(v, p.item)),
  }),
  defineFunction({
    name: "Take",
    description: "First n items.",
    params: z.object({ list, count }),
    fn: (p) => p.list.slice(0, p.count),
  }),
  defineFunction({
    name: "Slice",
    description: "Slice by start and end.",
    params: z.object({ list, start: count, end: count.optional() }),
    fn: (p) => p.list.slice(p.start, p.end),
  }),
  defineFunction({
    name: "Unique",
    description: "Distinct items.",
    params: z.object({ list }),
    fn: (p) =>
      p.list.filter((v, i, all) => all.findIndex((a) => same(a, v)) === i),
  }),
  defineFunction({
    name: "Reverse",
    description: "Reverse a list.",
    params: z.object({ list }),
    fn: (p) => [...p.list].reverse(),
  }),
  defineFunction({
    name: "Range",
    description: "Inclusive numeric range, at most 1000 values.",
    params: z.object({ start: number, end: number, step: number.default(1) }),
    fn: (p) =>
      p.step === 0 || Math.sign(p.end - p.start) * p.step < 0
        ? []
        : Array.from(
            {
              length: Math.min(
                1000,
                Math.max(0, Math.floor((p.end - p.start) / p.step) + 1),
              ),
            },
            (_, i) => p.start + i * p.step,
          ),
  }),
  defineFunction({
    name: "Pick",
    description: "Pick a random list item.",
    params: z.object({ list }),
    fn: (p) => p.list[Math.floor(Math.random() * p.list.length)] ?? null,
  }),
  defineFunction({
    name: "Shuffle",
    description: "Shuffle a copy.",
    params: z.object({ list }),
    fn: (p) => {
      const out = [...p.list];
      for (let i = out.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [out[i], out[j]] = [out[j], out[i]];
      }
      return out;
    },
  }),
  defineFunction({
    name: "Flatten",
    description: "Flatten one level.",
    params: z.object({ list }),
    fn: (p) => p.list.flat().slice(0, 1000),
  }),
  defineFunction({
    name: "Chunk",
    description: "Group into fixed-size lists.",
    params: z.object({ list, size: count.min(1) }),
    fn: (p) =>
      Array.from({ length: Math.ceil(p.list.length / p.size) }, (_, i) =>
        p.list.slice(i * p.size, (i + 1) * p.size),
      ),
  }),
  defineFunction({
    name: "Pow",
    description: "Exponentiation.",
    params: z.object({ base: number, exponent: number }),
    fn: (p) => Math.pow(p.base, p.exponent),
  }),
  defineFunction({
    name: "Sqrt",
    description: "Square root.",
    params: z.object({ value: number.min(0) }),
    fn: (p) => Math.sqrt(p.value),
  }),
  defineFunction({
    name: "Random",
    description: "Random number in a range.",
    params: z.object({ min: number, max: number }),
    fn: (p) => p.min + Math.random() * (p.max - p.min),
  }),
  defineFunction({
    name: "Clamp",
    description: "Bound a value.",
    params: z.object({ value: number, min: number, max: number }),
    fn: (p) => Math.max(p.min, Math.min(p.max, p.value)),
  }),
  defineFunction({
    name: "Percent",
    description: "Percent of total; null for zero denominator.",
    params: z.object({ part: number, total: number }),
    fn: (p) => (p.total ? (p.part / p.total) * 100 : null),
  }),
  defineFunction({
    name: "Lerp",
    description: "Linear interpolation.",
    params: z.object({ start: number, end: number, fraction: number }),
    fn: (p) => p.start + (p.end - p.start) * p.fraction,
  }),
  defineFunction({
    name: "Allocate",
    description: "Allocate total proportionally by weights.",
    params: z.object({
      total: number,
      weights: z.array(number.min(0)).max(1000),
    }),
    fn: (p) => {
      const sum = p.weights.reduce((a, b) => a + b, 0);
      return p.weights.map((w) => (sum ? (p.total * w) / sum : 0));
    },
  }),
  defineFunction({
    name: "Payment",
    description: "Loan payment; rate is per period.",
    params: z.object({
      principal: number,
      rate: number.min(0),
      periods: number.int().min(1),
    }),
    fn: (p) =>
      p.rate
        ? (p.principal * p.rate) / (1 - Math.pow(1 + p.rate, -p.periods))
        : p.principal / p.periods,
  }),
  defineFunction({
    name: "Convert",
    description: "Convert with an explicit factor and offset.",
    params: z.object({
      value: number,
      factor: number,
      offset: number.default(0),
    }),
    fn: (p) => p.value * p.factor + p.offset,
  }),
  defineFunction({
    name: "Format",
    description:
      "Format a number with fraction digits and optional ISO currency.",
    params: z.object({
      value: number,
      digits: number.int().min(0).max(10).default(2),
      currency: text.optional(),
      locale: text.default("en-US"),
    }),
    fn: (p) =>
      new Intl.NumberFormat(p.locale, {
        maximumFractionDigits: p.digits,
        ...(p.currency ? { style: "currency", currency: p.currency } : {}),
      }).format(p.value),
  }),
  defineFunction({
    name: "Join",
    description: "Join list items.",
    params: z.object({ list, separator: text.default(", ") }),
    fn: (p) => p.list.map(String).join(p.separator).slice(0, 16000),
  }),
  defineFunction({
    name: "Plural",
    description: "Choose singular or plural.",
    params: z.object({ count: number, singular: text, plural: text }),
    fn: (p) => (p.count === 1 ? p.singular : p.plural),
  }),
  defineFunction({
    name: "Pad",
    description: "Pad text on the left.",
    params: z.object({ text, length: count, character: text.default("0") }),
    fn: (p) => p.text.padStart(p.length, p.character),
  }),
  ...(["Upper", "Lower", "Trim", "Capitalize"] as const).map((name) =>
    defineFunction({
      name,
      description: `${name} text.`,
      params: z.object({ text }),
      fn: (p) =>
        name === "Upper"
          ? p.text.toUpperCase()
          : name === "Lower"
            ? p.text.toLowerCase()
            : name === "Trim"
              ? p.text.trim()
              : p.text.replace(/\b\p{L}/gu, (v) => v.toUpperCase()),
    }),
  ),
  defineFunction({
    name: "Split",
    description: "Split literal text.",
    params: z.object({ text, separator: text }),
    fn: (p) => p.text.split(p.separator).slice(0, 1000),
  }),
  defineFunction({
    name: "Replace",
    description: "Replace every literal occurrence.",
    params: z.object({ text, search: text.min(1), replacement: text }),
    fn: (p) => {
      let out = "";
      const pieces = p.text.split(p.search);
      for (let i = 0; i < pieces.length && out.length < 16000; i++) {
        if (i) out += p.replacement.slice(0, 16000 - out.length);
        out += pieces[i].slice(0, 16000 - out.length);
      }
      return out;
    },
  }),
  defineFunction({
    name: "Duration",
    description: "Seconds to minutes:seconds.",
    params: z.object({ seconds: number.min(0) }),
    fn: (p) =>
      `${Math.floor(p.seconds / 60)}:${String(Math.floor(p.seconds % 60)).padStart(2, "0")}`,
  }),
  defineFunction({
    name: "Default",
    description: "Fallback for null or empty.",
    params: z.object({ value, fallback: value }),
    fn: (p) => (p.value == null || p.value === "" ? p.fallback : p.value),
  }),
  defineFunction({
    name: "MixColor",
    description: "Mix named Nova tokens with a CSS color-mix expression.",
    params: z.object({
      first: z.enum([
        "primary",
        "foreground",
        "muted",
        "success",
        "warning",
        "info",
      ]),
      second: z.enum([
        "primary",
        "foreground",
        "muted",
        "success",
        "warning",
        "info",
      ]),
      fraction: number.min(0).max(1),
    }),
    fn: (p) =>
      `color-mix(in srgb, var(--${p.first}) ${p.fraction * 100}%, var(--${p.second}))`,
  }),
  defineFunction({
    name: "Now",
    description: "Current ISO timestamp.",
    params: z.object({}),
    fn: () => new Date().toISOString(),
  }),
  defineFunction({
    name: "Today",
    description: "Current UTC date.",
    params: z.object({}),
    fn: () => iso(new Date()),
  }),
  defineFunction({
    name: "FormatDate",
    description: "Localized date.",
    params: z.object({ date: text, locale: text.default("en-US") }),
    fn: (p) =>
      new Intl.DateTimeFormat(p.locale, { dateStyle: "medium" }).format(
        date(p.date),
      ),
  }),
  defineFunction({
    name: "AddDays",
    description: "Add UTC calendar days.",
    params: z.object({ date: text, days: number.int() }),
    fn: (p) => {
      const d = date(p.date);
      d.setUTCDate(d.getUTCDate() + p.days);
      return iso(d);
    },
  }),
  defineFunction({
    name: "AddMonths",
    description: "Add months, clamped to the target month.",
    params: z.object({ date: text, months: number.int() }),
    fn: (p) => {
      const d = date(p.date);
      const day = d.getUTCDate();
      d.setUTCDate(1);
      d.setUTCMonth(d.getUTCMonth() + p.months);
      const end = new Date(
        Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0),
      ).getUTCDate();
      d.setUTCDate(Math.min(day, end));
      return iso(d);
    },
  }),
  defineFunction({
    name: "DaysBetween",
    description: "Signed whole days between dates.",
    params: z.object({ first: text, second: text }),
    fn: (p) =>
      Math.trunc(
        (date(p.second).getTime() - date(p.first).getTime()) / 86400000,
      ),
  }),
  defineFunction({
    name: "MonthsBetween",
    description: "Signed calendar month difference.",
    params: z.object({ first: text, second: text }),
    fn: (p) =>
      (date(p.second).getUTCFullYear() - date(p.first).getUTCFullYear()) * 12 +
      date(p.second).getUTCMonth() -
      date(p.first).getUTCMonth(),
  }),
  defineFunction({
    name: "StartOf",
    description: "UTC start of day, Monday-based week, month or year.",
    params: z.object({
      date: text,
      unit: z.enum(["day", "week", "month", "year"]),
    }),
    fn: (p) => {
      const d = date(p.date);
      if (p.unit === "week")
        d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
      if (p.unit === "month" || p.unit === "year") d.setUTCDate(1);
      if (p.unit === "year") d.setUTCMonth(0);
      return iso(d);
    },
  }),
  defineFunction({
    name: "GroupBy",
    description: "Group records by a field.",
    params: z.object({ rows: records, field: text }),
    fn: (p) => {
      const out: Row = Object.create(null);
      for (const r of p.rows) {
        const key = String(r[p.field]);
        ((out[key] as Row[] | undefined) ?? ((out[key] = []) as Row[])).push(r);
      }
      return out;
    },
  }),
  defineFunction({
    name: "Pivot",
    description:
      "Sum a numeric field by group and category, within this result only.",
    params: z.object({
      rows: records,
      group: text,
      category: text,
      value: text,
    }),
    fn: (p) => {
      const out = new Map<string, Row>();
      for (const r of p.rows) {
        const key = String(r[p.group]);
        const row = out.get(key) ?? { [p.group]: key };
        const col = String(r[p.category]);
        if (["__proto__", "constructor", "prototype"].includes(col)) continue;
        row[col] = numeric(row[col]) + numeric(r[p.value]);
        out.set(key, row);
      }
      return [...out.values()];
    },
  }),
  defineFunction({
    name: "Top",
    description: "Top n records by numeric field.",
    params: z.object({ rows: records, field: text, count }),
    fn: (p) =>
      [...p.rows]
        .sort((a, b) => numeric(b[p.field]) - numeric(a[p.field]))
        .slice(0, p.count),
  }),
  defineFunction({
    name: "Between",
    description: "Filter records inclusively by numeric field.",
    params: z.object({ rows: records, field: text, min: number, max: number }),
    fn: (p) =>
      p.rows.filter(
        (r) =>
          (typeof r[p.field] === "number" ||
            (typeof r[p.field] === "string" &&
              decimal.test(r[p.field] as string))) &&
          numeric(r[p.field]) >= p.min &&
          numeric(r[p.field]) <= p.max,
      ),
  }),
  defineFunction({
    name: "Bucket",
    description: "Numeric bucket lower bound.",
    params: z.object({ value: number, width: number.positive() }),
    fn: (p) => Math.floor(p.value / p.width) * p.width,
  }),
  defineFunction({
    name: "Cumulative",
    description: "Running totals over a list.",
    params: z.object({ values: z.array(number).max(1000) }),
    fn: (p) => {
      let sum = 0;
      return p.values.map((v) => (sum += v));
    },
  }),
  defineFunction({
    name: "MovingAvg",
    description: "Trailing average over this list.",
    params: z.object({
      values: z.array(number).max(1000),
      window: count.min(1),
    }),
    fn: (p) =>
      p.values.map((_, i) =>
        numeric(
          stats(p.values.slice(Math.max(0, i - p.window + 1), i + 1)).mean,
        ),
      ),
  }),
  defineFunction({
    name: "Change",
    description: "Percentage change; null from zero.",
    params: z.object({ current: number, previous: number }),
    fn: (p) =>
      p.previous
        ? ((p.current - p.previous) / Math.abs(p.previous)) * 100
        : null,
  }),
  defineFunction({
    name: "Histogram",
    description: "Count values in numeric buckets.",
    params: z.object({
      values: z.array(number).max(1000),
      width: number.positive(),
    }),
    fn: (p) => {
      const counts = new Map<number, number>();
      for (const n of p.values) {
        const b = Math.floor(n / p.width) * p.width;
        counts.set(b, (counts.get(b) ?? 0) + 1);
      }
      return [...counts]
        .sort((a, b) => a[0] - b[0])
        .map(([bucket, count]) => ({ bucket, count }));
    },
  }),
  defineFunction({
    name: "Stats",
    description: "Count, sum, mean, min, max, median of this list.",
    params: z.object({ values: list }),
    fn: (p) => stats(p.values),
  }),
];
