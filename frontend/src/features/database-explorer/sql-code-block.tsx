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
      className="sql-code-block hljs overflow-x-auto rounded-xl border border-border/70 bg-muted/35 p-4 font-mono text-[13px] leading-6"
    >
      {highlighted ? (
        <code dangerouslySetInnerHTML={{ __html: highlighted }} />
      ) : (
        <code>{source}</code>
      )}
    </pre>
  );
}
