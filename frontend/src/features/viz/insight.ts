import type { PresModel, PresModelColumn } from "./types";

/**
 * The answer a view gives, computed from its own rows. Every number in it
 * comes from the presentation model the engine returned; nothing is
 * inferred beyond ranking, sums, shares and first/last comparisons, so the
 * sentence is as true as the chart.
 */

type Cell = string | number | null;

export type ViewInsight = {
  /** "Mobile App" leads at 3.37T: subject, verb, value split into number and unit.
   * ``marked`` is true when the subject is the highlighted mark, so the
   * headline and the chart share the highlight color. */
  lead: { subject: string; verb: string; num: string; unit: string; marked: boolean };
  /** One supporting sentence, or empty. */
  detail: string;
  /** The mark the answer is about; renderers draw it in the highlight color. */
  highlight: { dimension: string; value: Cell } | null;
};

const ADDITIVE = new Set(["sum", "count", "distinct_count"]);

/** 3368049065451 → { num: "3.37", unit: "T" }; 0.153 → { num: "0.15", unit: "" }. */
export function splitMeasure(value: number): { num: string; unit: string } {
  const abs = Math.abs(value);
  const scales: [number, string][] = [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  for (const [size, unit] of scales) {
    if (abs >= size) return { num: trim(value / size), unit };
  }
  return { num: trim(value), unit: "" };
}

function trim(value: number): string {
  const abs = Math.abs(value);
  const digits = abs >= 100 ? 0 : 2;
  const fixed = value.toFixed(digits);
  return digits ? fixed.replace(/\.?0+$/, "") : fixed;
}

export function formatNumber(value: number): string {
  const { num, unit } = splitMeasure(value);
  return `${num}${unit}`;
}

function percent(part: number, whole: number): string {
  return `${(Math.round((part / whole) * 1000) / 10).toFixed(1)}%`;
}

/** "sum(recognized_revenue)" reads as "SUM(recognized_revenue)", as on the shelf. */
export function measureCaption(label: string): string {
  const match = /^([a-z_]+)\((.*)\)$/.exec(label);
  if (!match) return label;
  const prefix: Record<string, string> = { distinct_count: "CNTD", count: "CNT", avg: "AVG" };
  return `${prefix[match[1]] ?? match[1].toUpperCase()}(${match[2]})`;
}

function numeric(value: Cell): number | null {
  if (value === null || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function isTemporal(column: PresModelColumn): boolean {
  return column.field_type === "temporal" || Boolean(column.date_part);
}

function label(value: Cell): string {
  if (value === null) return "Null";
  const text = String(value);
  // ISO dates read as "23 Jun 2026"; anything else as written.
  const date = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
  if (!date) return text;
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(date[2]) - 1];
  return month ? `${Number(date[3])} ${month} ${date[1]}` : text;
}

export function describeView(presmodel: PresModel, selected?: Cell): ViewInsight | null {
  const columns = presmodel.columns;
  const dims = columns.map((column, index) => ({ column, index })).filter((item) => item.column.role === "dimension");
  const measures = columns.map((column, index) => ({ column, index })).filter((item) => item.column.role === "measure");
  if (!measures.length || !presmodel.rows.length) return null;
  const measure = measures[0];
  const caption = measureCaption(measure.column.label);

  if (!dims.length) {
    const value = numeric(presmodel.rows[0][measure.index]);
    if (value === null || presmodel.rows.length !== 1) return null;
    const { num, unit } = splitMeasure(value);
    return { lead: { subject: caption, verb: "is", num, unit, marked: false }, detail: "", highlight: null };
  }
  if (dims.length !== 1) return null;
  const dim = dims[0];
  const points = presmodel.rows
    .map((row) => ({ key: row[dim.index], value: numeric(row[measure.index]) }))
    .filter((point): point is { key: Cell; value: number } => point.value !== null);
  if (points.length < 2) return null;

  if (isTemporal(dim.column)) return describeSeries(points, caption, dim.column.label);
  return describeRanking(points, dim.column.label, measure.column, selected);
}

function describeRanking(
  points: { key: Cell; value: number }[],
  dimension: string,
  measure: PresModelColumn,
  selected?: Cell,
): ViewInsight {
  const ranked = [...points].sort((a, b) => b.value - a.value);
  const leader = ranked[0];
  const runner = ranked[1];
  const last = ranked[ranked.length - 1];
  const total = ranked.reduce((sum, point) => sum + point.value, 0);
  const additive =
    ADDITIVE.has(measure.aggregation ?? "") && ranked.every((point) => point.value >= 0) && total > 0;
  const average = total / ranked.length;
  const chosen = ranked.find((point) => selected !== undefined && selected !== null && point.key === selected) ?? leader;
  const rank = ranked.indexOf(chosen) + 1;
  const { num, unit } = splitMeasure(chosen.value);

  if (chosen === leader) {
    const share = additive ? `${percent(leader.value, total)} of the ${formatNumber(total)} total. ` : "";
    const next = `${label(runner.key)} is next, ${formatNumber(leader.value - runner.value)} behind`;
    const tail = ranked.length > 2
      ? `; ${label(last.key)} ${additive ? `trails at ${percent(last.value, total)}` : `is lowest at ${formatNumber(last.value)}`}.`
      : ".";
    return {
      lead: { subject: label(leader.key), verb: "leads at", num, unit, marked: true },
      detail: `${share}${next}${tail}`,
      highlight: { dimension, value: leader.key },
    };
  }
  const ordinal = rank === 2 ? "2nd" : rank === 3 ? "3rd" : `${rank}th`;
  const share = additive ? `${percent(chosen.value, total)} of ${formatNumber(total)}. ` : "";
  const versus = chosen.value >= average
    ? `${formatNumber(chosen.value - average)} above`
    : `${formatNumber(average - chosen.value)} below`;
  return {
    lead: { subject: label(chosen.key), verb: `ranks ${ordinal} of ${ranked.length} at`, num, unit, marked: true },
    detail: `${share}${formatNumber(leader.value - chosen.value)} behind ${label(leader.key)}, and ${versus} the average of ${formatNumber(average)}.`,
    highlight: { dimension, value: chosen.key },
  };
}

function describeSeries(points: { key: Cell; value: number }[], caption: string, dimension: string): ViewInsight {
  const first = points[0];
  const last = points[points.length - 1];
  const low = points.reduce((best, point) => (point.value < best.value ? point : best), first);
  const high = points.reduce((best, point) => (point.value > best.value ? point : best), first);
  const average = points.reduce((sum, point) => sum + point.value, 0) / points.length;
  const change = first.value !== 0 ? (last.value - first.value) / Math.abs(first.value) : 0;

  if (Math.abs(change) >= 0.05 && first.value !== 0) {
    const { num, unit } = splitMeasure(last.value);
    return {
      lead: { subject: caption, verb: change > 0 ? "rose to" : "fell to", num, unit, marked: false },
      detail: `${change > 0 ? "Up" : "Down"} ${percent(Math.abs(last.value - first.value), Math.abs(first.value))} from ${formatNumber(first.value)} on ${label(first.key)}. Peak ${formatNumber(high.value)} on ${label(high.key)}; low ${formatNumber(low.value)} on ${label(low.key)}.`,
      highlight: { dimension, value: last.key },
    };
  }
  const { num, unit } = splitMeasure(average);
  return {
    lead: { subject: caption, verb: "holds near", num, unit, marked: false },
    detail: `Every point lands between ${formatNumber(low.value)} and ${formatNumber(high.value)}. The low, ${formatNumber(low.value)} on ${label(low.key)}, sits ${formatNumber(average - low.value)} under the average.`,
    highlight: { dimension, value: low.key },
  };
}

/** The first table a query reads, as `DB.table`, when it names one plainly. */
export function sourceOf(sql: string | null | undefined): string | null {
  if (!sql) return null;
  const match = /\bFROM\s+((?:`[^`]+`|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:`[^`]+`|[A-Za-z_][\w$]*))*)/i.exec(sql);
  if (!match) return null;
  return match[1].replace(/`/g, "").replace(/\s+/g, "");
}
