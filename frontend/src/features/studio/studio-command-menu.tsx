import { useEffect, useRef } from "react";
import { PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { commandOptionId, type StudioCommand } from "./studio-commands";

/**
 * The slash-command list. It renders in a portal above the composer, so
 * opening, filtering and closing it never move anything on the page. Focus
 * stays in the message box; the box drives the list through
 * aria-activedescendant.
 */
export function StudioCommandMenu({
  listId,
  commands,
  active,
  onActiveChange,
  onChoose,
  anchorRef,
}: {
  listId: string;
  commands: StudioCommand[];
  active: number;
  onActiveChange: (index: number) => void;
  onChoose: (command: StudioCommand) => void;
  anchorRef: React.RefObject<HTMLElement | null>;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const option = listRef.current?.querySelector<HTMLElement>(
      `#${CSS.escape(commandOptionId(listId, active))}`,
    );
    option?.scrollIntoView({ block: "nearest" });
  }, [active, listId]);

  const groups = [...new Set(commands.map((command) => command.group))];

  return (
    <PopoverContent
      side="top"
      align="start"
      sideOffset={8}
      collisionPadding={8}
      data-testid="studio-command-menu"
      className="flex w-(--radix-popover-trigger-width) max-w-[calc(100vw-1rem)] flex-col overflow-hidden rounded-xl p-0 shadow-lg"
      onOpenAutoFocus={(event) => event.preventDefault()}
      onCloseAutoFocus={(event) => event.preventDefault()}
      onInteractOutside={(event) => {
        if (anchorRef.current?.contains(event.target as Node)) event.preventDefault();
      }}
      // Keep focus in the message box when the list is clicked.
      onMouseDown={(event) => event.preventDefault()}
    >
      <div
        ref={listRef}
        id={listId}
        role="listbox"
        aria-label="Commands"
        className="max-h-[min(18rem,var(--radix-popover-content-available-height))] overflow-y-auto overscroll-contain p-1.5"
      >
        {groups.map((group) => (
          <div key={group} role="group" aria-labelledby={`${listId}-${group}`}>
            <div
              id={`${listId}-${group}`}
              className="px-2.5 pt-1.5 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase"
            >
              {group}
            </div>
            {commands.map((command, index) =>
              command.group !== group ? null : (
                <div
                  key={command.name}
                  id={commandOptionId(listId, index)}
                  role="option"
                  aria-selected={index === active}
                  data-active={index === active || undefined}
                  className={cn(
                    "cursor-pointer rounded-lg px-2.5 py-2 text-popover-foreground",
                    "data-[active]:bg-accent data-[active]:text-accent-foreground",
                  )}
                  onMouseMove={() => {
                    if (index !== active) onActiveChange(index);
                  }}
                  onClick={() => onChoose(command)}
                >
                  <div className="flex min-w-0 items-baseline gap-2">
                    <span className="truncate font-mono text-sm">{command.name}</span>
                    {command.argumentHint ? (
                      <span className="shrink-0 font-mono text-xs text-muted-foreground">
                        {command.argumentHint}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-0.5 truncate text-xs text-muted-foreground">
                    {command.description}
                  </div>
                </div>
              ),
            )}
          </div>
        ))}
      </div>
    </PopoverContent>
  );
}
