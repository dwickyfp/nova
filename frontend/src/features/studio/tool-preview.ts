/** Leading words of a SQL statement. Most tools preview in prose, not SQL. */
const SQL_LEADING_WORDS = new Set([
  "ALTER", "ANALYZE", "CALL", "CANCEL", "COPY", "CREATE", "DELETE", "DESC",
  "DESCRIBE", "DROP", "EXPLAIN", "GRANT", "INSERT", "LOAD", "MERGE", "REFRESH",
  "REVOKE", "SELECT", "SET", "SHOW", "SUBMIT", "TRUNCATE", "UPDATE", "USE",
  "VALUES", "WITH",
]);

/** Whether a tool preview is a SQL statement rather than a sentence. */
export function isSqlPreview(preview: string): boolean {
  const start = preview
    .replace(/^(\s|\(|--[^\n]*\n|\/\*[\s\S]*?\*\/)+/, "")
    .match(/^[A-Za-z]+/)?.[0];
  return start ? SQL_LEADING_WORDS.has(start.toUpperCase()) : false;
}
