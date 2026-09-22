import hljs from "highlight.js/lib/core";
import sql from "highlight.js/lib/languages/sql";

hljs.registerLanguage("sql", sql);

export function highlightExplorerSql(source: string): string | null {
  try {
    return hljs
      .highlight(source, { language: "sql" })
      .value.replace(/`([^`\n]+)`/g, '<span class="hljs-attr">`$1`</span>');
  } catch {
    return null;
  }
}
