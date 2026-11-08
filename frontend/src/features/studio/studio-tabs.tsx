import { cn } from "@/lib/utils";

export type StudioTab = "agent" | "dataviz";

const TABS: { id: StudioTab; label: string }[] = [
  { id: "agent", label: "Agent" },
  { id: "dataviz", label: "Dataviz" },
];

/**
 * Studio's two workspaces as one segmented control: Agent (the native chat)
 * and Dataviz (Superset with Nova's assistant). Arrow keys move between them,
 * as in any tab list.
 */
export function StudioTabs({ tab, onSelect }: { tab: StudioTab; onSelect: (tab: StudioTab) => void }) {
  return (
    <div className="flex shrink-0 justify-center px-4 pt-3 pb-2">
      <div
        role="tablist"
        aria-label="Studio workspace"
        className="inline-flex items-center gap-1 rounded-full border bg-card p-1 shadow-sm"
        onKeyDown={(event) => {
          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
          event.preventDefault();
          const index = TABS.findIndex((item) => item.id === tab);
          const next = TABS[(index + (event.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
          onSelect(next.id);
          window.requestAnimationFrame(() =>
            document.getElementById(`studio-tab-${next.id}`)?.focus(),
          );
        }}
      >
        {TABS.map((item) => {
          const selected = item.id === tab;
          return (
            <button
              key={item.id}
              id={`studio-tab-${item.id}`}
              type="button"
              role="tab"
              aria-selected={selected}
              tabIndex={selected ? 0 : -1}
              onClick={() => onSelect(item.id)}
              className={cn(
                "min-h-8 min-w-24 rounded-full px-4 text-sm font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                selected
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {item.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
