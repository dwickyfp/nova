/** Leading words of a SQL statement. Most tools preview in prose, not SQL. */
const SQL_LEADING_WORDS = new Set([
  "ALTER", "ANALYZE", "CALL", "CANCEL", "COPY", "CREATE", "DELETE", "DESC",
  "DESCRIBE", "DROP", "EXPLAIN", "GRANT", "INSERT", "LOAD", "MERGE", "REFRESH",
  "REVOKE", "SELECT", "SET", "SHOW", "SUBMIT", "TRUNCATE", "UPDATE", "USE",
  "VALUES", "WITH",
]);

/** Symbols a statement has and an English request does not. */
const SQL_STRUCTURE = /[*=;(`]/;

/**
 * Whether a tool preview is a SQL statement rather than a sentence. Several
 * leading words are also English ("Show revenue by order status."), so a
 * preview that ends like a sentence and has no statement structure is prose.
 */
export function isSqlPreview(preview: string): boolean {
  const body = preview.replace(/^(\s|\(|--[^\n]*\n|\/\*[\s\S]*?\*\/)+/, "");
  const start = body.match(/^[A-Za-z]+/)?.[0];
  if (!start || !SQL_LEADING_WORDS.has(start.toUpperCase())) return false;
  // "Call ..." also opens plain sentences; SQL CALL names a routine and its arguments.
  if (start.toUpperCase() === "CALL") return /^call\s+[\w.`"]+\s*\(/i.test(body);
  return !(/[.?!]\s*$/.test(body) && !SQL_STRUCTURE.test(body));
}
