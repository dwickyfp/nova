import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type HTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { ChevronRight, File, Folder, FolderOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

/**
 * File Tree (AI Elements pattern): folders are Collapsibles, files are Buttons.
 * `FileTree` keeps which folders are open and which item is selected; it can
 * be controlled (`expanded`, `selectedPath`) or left to manage itself.
 * Rows sit in one column with a 16px indent per level. Selection is not
 * painted: it marks where new items go, and the caller highlights what is
 * open (a row's `className`).
 */
type FileTreeContextValue = {
  expanded: Set<string>;
  toggle: (path: string, open?: boolean) => void;
  selectedPath: string | null;
  select: (path: string) => void;
};

const FileTreeContext = createContext<FileTreeContextValue | null>(null);
const DepthContext = createContext(0);

function useFileTree() {
  const value = useContext(FileTreeContext);
  if (!value) throw new Error("FileTree parts must be inside <FileTree>");
  return value;
}

export function FileTree({
  expanded,
  defaultExpanded,
  onExpandedChange,
  selectedPath = null,
  onSelect,
  className,
  children,
  ...props
}: {
  expanded?: Set<string>;
  defaultExpanded?: Set<string>;
  onExpandedChange?: (expanded: Set<string>) => void;
  selectedPath?: string | null;
  onSelect?: (path: string) => void;
  children: ReactNode;
} & Omit<HTMLAttributes<HTMLDivElement>, "onSelect">) {
  const [own, setOwn] = useState<Set<string>>(defaultExpanded ?? new Set());
  const current = expanded ?? own;
  const ref = useRef<HTMLDivElement>(null);

  const toggle = useCallback(
    (path: string, open?: boolean) => {
      const next = new Set(current);
      const shouldOpen = open ?? !next.has(path);
      if (shouldOpen) next.add(path);
      else next.delete(path);
      if (!expanded) setOwn(next);
      onExpandedChange?.(next);
    },
    [current, expanded, onExpandedChange],
  );

  const value = useMemo(
    () => ({ expanded: current, toggle, selectedPath, select: (path: string) => onSelect?.(path) }),
    [current, toggle, selectedPath, onSelect],
  );

  // Up/down move between visible rows, like a file explorer.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const rows = Array.from(
      ref.current?.querySelectorAll<HTMLElement>("[data-file-tree-row]") ?? [],
    );
    const index = rows.indexOf(document.activeElement as HTMLElement);
    if (index < 0) return;
    const next = rows[index + (event.key === "ArrowDown" ? 1 : -1)];
    if (next) {
      event.preventDefault();
      next.focus();
    }
  };

  return (
    <FileTreeContext.Provider value={value}>
      <div
        ref={ref}
        role="tree"
        onKeyDown={onKeyDown}
        className={cn("flex min-w-0 flex-col text-sm", className)}
        {...props}
      >
        {children}
      </div>
    </FileTreeContext.Provider>
  );
}

const rowClass =
  "h-8 w-full min-w-0 justify-start gap-2 rounded-md px-2 font-normal text-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring";

export function FileTreeFolder({
  path,
  name,
  icon,
  actions,
  children,
  className,
  rowProps,
}: {
  path: string;
  name: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  rowProps?: HTMLAttributes<HTMLButtonElement>;
}) {
  const { expanded, toggle, selectedPath, select } = useFileTree();
  const depth = useContext(DepthContext);
  const open = expanded.has(path);
  return (
    <Collapsible open={open} onOpenChange={(next) => toggle(path, next)} className={className}>
      <div role="treeitem" aria-expanded={open} aria-selected={selectedPath === path}
           className="group/row relative flex items-center">
        <CollapsibleTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-file-tree-row=""
            className={cn(rowClass, actions && "pr-9")}
            style={{ paddingLeft: 8 + depth * 16 }}
            onClick={() => select(path)}
            onKeyDown={(event) => {
              if (event.key === "ArrowRight" && !open) {
                event.preventDefault();
                toggle(path, true);
              } else if (event.key === "ArrowLeft" && open) {
                event.preventDefault();
                toggle(path, false);
              }
            }}
            {...rowProps}
          >
            <ChevronRight
              aria-hidden
              className={cn("size-3.5 shrink-0 text-muted-foreground transition-transform",
                open && "rotate-90")}
            />
            {icon ?? <FileTreeIcon>{open ? <FolderOpen /> : <Folder />}</FileTreeIcon>}
            <FileTreeName>{name}</FileTreeName>
          </Button>
        </CollapsibleTrigger>
        {actions ? <FileTreeActions>{actions}</FileTreeActions> : null}
      </div>
      <CollapsibleContent role="group">
        <DepthContext.Provider value={depth + 1}>{children}</DepthContext.Provider>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function FileTreeFile({
  path,
  name,
  icon,
  actions,
  onOpen,
  className,
  rowProps,
}: {
  path: string;
  name: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  onOpen?: () => void;
  className?: string;
  rowProps?: HTMLAttributes<HTMLButtonElement>;
}) {
  const { selectedPath, select } = useFileTree();
  const depth = useContext(DepthContext);
  const selected = selectedPath === path;
  return (
    <div role="treeitem" aria-selected={selected}
         className={cn("group/row relative flex items-center", className)}>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        data-file-tree-row=""
        className={cn(rowClass, actions && "pr-9")}
        // Files line up with folder names: chevron (14) + gap (8).
        style={{ paddingLeft: 8 + depth * 16 + 22 }}
        onClick={() => {
          select(path);
          onOpen?.();
        }}
        {...rowProps}
      >
        {icon ?? (
          <FileTreeIcon>
            <File />
          </FileTreeIcon>
        )}
        <FileTreeName>{name}</FileTreeName>
      </Button>
      {actions ? <FileTreeActions>{actions}</FileTreeActions> : null}
    </div>
  );
}

export function FileTreeIcon({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("flex size-4 shrink-0 items-center justify-center text-muted-foreground [&>svg]:size-4",
        className)}
    >
      {children}
    </span>
  );
}

export function FileTreeName({ children }: { children: ReactNode }) {
  return <span className="min-w-0 truncate">{children}</span>;
}

/** Row actions: shown on hover or keyboard focus, aligned to the row's end. */
export function FileTreeActions({ children }: { children: ReactNode }) {
  return (
    <span className="absolute right-1 flex items-center opacity-0 transition-opacity group-focus-within/row:opacity-100 group-hover/row:opacity-100">
      {children}
    </span>
  );
}
