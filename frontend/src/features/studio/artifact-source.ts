import { format as formatSql } from "sql-formatter";

/**
 * Reading helpers for stored SQL.
 *
 * Studio no longer looks for "the SQL behind a result" in the browser: a result
 * reaches Dataviz only through a server-verified reference to the exact tool
 * step that produced it (docs/arch-19-studio-dataviz.md, D1). Searching earlier
 * turns for a statement could attach an unrelated query to a chart.
 */

/** Format stored SQL for reading without ever mutating the persisted query. */
export function formatArtifactSql(sql: string): string {
  try {
    return formatSql(sql, {
      language: "mysql",
      keywordCase: "upper",
      tabWidth: 2,
    });
  } catch {
    return sql;
  }
}
