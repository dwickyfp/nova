import { highlightExplorerSql } from "./sql-highlighter";

export function SqlCodeBlock({
  source,
  label = "SQL",
}: {
  source: string;
  label?: string;
}) {
  const highlighted = highlightExplorerSql(source);

  return (
    <pre
      aria-label={`${label} syntax highlighted code`}
      className="sql-code-block hljs overflow-x-auto rounded-lg border border-surface-border bg-surface-1 p-4 font-mono text-xs leading-5"
    >
      {highlighted ? (
        <code dangerouslySetInnerHTML={{ __html: highlighted }} />
      ) : (
        <code>{source}</code>
      )}
    </pre>
  );
}
