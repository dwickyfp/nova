import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { BookMarked } from "lucide-react";
import type { DocsSearchHit } from "@/features/docs/content";

type DocsModule = typeof import("@/features/docs/content");

/**
 * Documentation pages matching the Home search. The docs index is loaded only
 * once the user types, so Home does not carry every page's text.
 */
export function DocsMatches({ query }: { query: string }) {
  const [docs, setDocs] = useState<DocsModule | null>(null);
  const trimmed = query.trim();

  useEffect(() => {
    if (!trimmed || docs) return;
    let active = true;
    void import("@/features/docs/content").then((module) => {
      if (active) setDocs(module);
    });
    return () => {
      active = false;
    };
  }, [trimmed, docs]);

  if (!trimmed || !docs) return null;
  const hits: DocsSearchHit[] = docs.searchDocs(trimmed, undefined, 4);
  if (hits.length === 0) return null;

  return (
    <div className="max-w-5xl space-y-2">
      <h2 className="text-sm text-muted-foreground">From the docs</h2>
      <ul className="grid gap-2 md:grid-cols-2">
        {hits.map(({ page, heading }) => (
          <li key={docs.docsPath(page)}>
            <Link
              {...docs.docsLink(page)}
              hash={heading?.id}
              className="flex items-start gap-3 rounded-lg border border-surface-border bg-card px-4 py-3 transition-colors hover:bg-surface-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <BookMarked className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">
                  {page.title}
                </span>
                <span className="line-clamp-1 text-xs text-muted-foreground">
                  {heading?.text ?? page.description}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
