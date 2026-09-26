import type { TableBlock } from "./types";

type TableNode = {
  type: string;
  tagName?: string;
  value?: string;
  children?: TableNode[];
};

const normalize = (text: string) => text.replace(/\s+/g, " ").trim();

function nodeText(node: TableNode): string {
  return node.value ?? node.children?.map(nodeText).join("") ?? "";
}

function tableRows(node: TableNode): string[][] {
  if (node.tagName === "tr") {
    return [(node.children ?? [])
      .filter((child) => child.tagName === "th" || child.tagName === "td")
      .map((child) => normalize(nodeText(child)))];
  }
  return node.children?.flatMap(tableRows) ?? [];
}

function sameCell(text: string, value: string | number | null, column: string): boolean {
  if (text === normalize(String(value ?? "")) || (value === null && text === "null")) return true;
  if (value === null || String(value).trim() === "") return false;
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || Math.abs(numeric) > Number.MAX_SAFE_INTEGER) return false;

  // Recognize the display formats used by the verified comparison renderer.
  for (const locale of ["en-US", "id-ID"]) {
    if (/(?:percent|percentage|pct|rate|growth|margin)/i.test(column)) {
      const points = Math.abs(numeric) > 1 ? numeric : numeric * 100;
      const percent = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(points);
      if (text === `${percent}%`) return true;
    } else if (/(?:count|quantity|units)/i.test(column) && Number.isInteger(numeric)) {
      if (text === new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(numeric)) return true;
    } else if (/(?:revenue|amount|profit|cost|price)/i.test(column) && /^-?\d+(?:\.\d{1,2})?$/.test(String(value))) {
      if (text === new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(numeric)) return true;
    }
  }
  return false;
}

export function matchesResultTable(node: TableNode | undefined, tables: TableBlock[]): boolean {
  if (!node || !tables.length) return false;
  const [headers, ...rows] = tableRows(node);
  if (!headers?.length || !rows.length || new Set(headers).size !== headers.length) return false;

  return tables.some((table) => {
    if (headers.length !== table.columns.length || rows.length !== table.rows.length) return false;
    const indexes = headers.map((header) => table.columns.findIndex((column) => normalize(column) === header));
    if (indexes.includes(-1) || new Set(indexes).size !== indexes.length) return false;
    const matched = new Set<number>();
    return rows.every((row) => {
      if (row.length !== headers.length) return false;
      const index = table.rows.findIndex((candidate, candidateIndex) =>
        !matched.has(candidateIndex)
        && candidate.length === table.columns.length
        && row.every((text, cellIndex) => sameCell(text, candidate[indexes[cellIndex]], headers[cellIndex])),
      );
      if (index < 0) return false;
      matched.add(index);
      return true;
    });
  });
}
