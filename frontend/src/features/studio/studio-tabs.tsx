import { cn } from "@/lib/utils";

export type StudioTab = "agent" | "dataviz";

const TABS: { id: StudioTab; label: string }[] = [
  { id: "agent", label: "Agent" },
  { id: "dataviz", label: "Dataviz" },
];

/**
 * Studio's two workspaces as one floating segmented control: Agent (the
 * native chat) and Dataviz (Superset with Nova's assistant). It floats over
 * the workspace instead of taking a header row; the selected plate slides
 * between the two tabs. Arrow keys move between them, as in any tab list.
 *
 * `reserveRow` is for pages with their own header (Artifacts, News,
 * Capabilities, editors): where the Studio pane (`@container/studio`) is
 * narrower than 64rem the switch takes its own row, so it never covers a
 * title or a button such as **Edit chart**.
 */
export function StudioTabs({
  tab,
  onSelect,
  reserveRow = false,
}: {
  tab: StudioTab;
  onSelect: (tab: StudioTab) => void;
  reserveRow?: boolean;
}) {
  return (
    <div
      data-reserve-row={reserveRow || undefined}
      className={cn(
        "pointer-events-none z-20 flex justify-center px-4",
        reserveRow
          ? "relative shrink-0 pt-3 @5xl/studio:absolute @5xl/studio:inset-x-0 @5xl/studio:top-3 @5xl/studio:pt-0"
          : "absolute inset-x-0 top-3",
      )}
    >
      <StudioTabSwitch tab={tab} onSelect={onSelect} className="pointer-events-auto" />
    </div>
  );
}

/** The switch itself, for a workspace that places it (Dataviz docks it in Superset's bar). */
export function StudioTabSwitch({
  tab,
  onSelect,
  className,
}: {
  tab: StudioTab;
  onSelect: (tab: StudioTab) => void;
  className?: string;
}) {
  const index = TABS.findIndex((item) => item.id === tab);
  return (
    <div
      role="tablist"
      aria-label="Studio workspace"
      className={cn("nova-mode-switch relative grid grid-cols-2 rounded-full p-1", className)}
      onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const next = TABS[(index + (event.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
        onSelect(next.id);
        window.requestAnimationFrame(() =>
          document.getElementById(`studio-tab-${next.id}`)?.focus(),
        );
      }}
    >
      <span
        aria-hidden="true"
        className="nova-mode-plate absolute inset-y-1 left-1 w-[calc(50%-4px)] rounded-full"
        style={{ transform: `translateX(${index * 100}%)` }}
      />
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
              "relative h-8 w-24 rounded-full text-sm font-medium transition-colors duration-200",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              selected ? "text-primary-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
