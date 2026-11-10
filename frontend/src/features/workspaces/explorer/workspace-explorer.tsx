import { useEffect, useMemo, useState, type ComponentType, type ReactNode } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileCode,
  FileCode2,
  FileSpreadsheet,
  FolderInput,
  FolderPlus,
  MoreHorizontal,
  NotebookPen,
  Pencil,
  Share2,
  Trash2,
  User,
  Users,
} from "lucide-react";
import { toast } from "sonner";
import {
  FileTree,
  FileTreeFile,
  FileTreeFolder,
  FileTreeIcon,
} from "@/components/ai-elements/file-tree";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { isEditableName, isNotebookName } from "../notebook/names";
import type { WorkspaceEntry } from "../types";
import { canEditSpace, workspaceApi, type WorkspaceSpace } from "./api";
import type { ExplorerController, Place } from "./controller";
import { ShareDialog } from "./share-dialog";
import { childrenOf, filterEntries, moveTargets, subtree } from "./tree-data";

type MenuItem =
  | { kind: "separator" }
  | { kind: "label"; text: string }
  | {
      kind: "item";
      label: string;
      icon: ComponentType<{ className?: string }>;
      onSelect: () => void;
      disabled?: boolean;
      destructive?: boolean;
    };

const rootKey = (space: WorkspaceSpace) => `root:${space.id}`;
const entryKey = (space: WorkspaceSpace, path: string) => `${space.id}:${path}`;

function entryIcon(entry: WorkspaceEntry) {
  const Icon = isNotebookName(entry.name)
    ? NotebookPen
    : isEditableName(entry.name)
      ? FileCode
      : FileSpreadsheet;
  return (
    <FileTreeIcon>
      <Icon />
    </FileTreeIcon>
  );
}

/** One menu, shown on right-click and from the row's "⋯" button. */
function RowMenu({ items, children }: { items: MenuItem[]; children: ReactNode }) {
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent className="w-56">
        {items.map((item, index) =>
          item.kind === "separator" ? (
            <ContextMenuSeparator key={index} />
          ) : item.kind === "label" ? (
            <ContextMenuLabel key={index} className="text-xs font-normal text-muted-foreground">
              {item.text}
            </ContextMenuLabel>
          ) : (
            <ContextMenuItem
              key={index}
              disabled={item.disabled}
              onSelect={item.onSelect}
              className={item.destructive ? "text-destructive focus:text-destructive" : undefined}
            >
              <item.icon className="size-4" />
              {item.label}
            </ContextMenuItem>
          ),
        )}
      </ContextMenuContent>
    </ContextMenu>
  );
}

function RowMenuButton({ items, label }: { items: MenuItem[]; label: string }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          size="icon"
          variant="ghost"
          className="size-6 text-muted-foreground hover:text-foreground"
          aria-label={label}
        >
          <MoreHorizontal className="size-3.5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        {items.map((item, index) =>
          item.kind === "separator" ? (
            <DropdownMenuSeparator key={index} />
          ) : item.kind === "label" ? (
            <DropdownMenuLabel key={index} className="text-xs font-normal text-muted-foreground">
              {item.text}
            </DropdownMenuLabel>
          ) : (
            <DropdownMenuItem
              key={index}
              disabled={item.disabled}
              onSelect={item.onSelect}
              className={item.destructive ? "text-destructive focus:text-destructive" : undefined}
            >
              <item.icon className="size-4" />
              {item.label}
            </DropdownMenuItem>
          ),
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * The Workspaces file tree: "Personal Workspace" first, then every shared
 * workspace the person can open. Right-click (or the row's "⋯") creates,
 * renames, moves and deletes; on a shared workspace the owner also shares,
 * renames and deletes it.
 */
export function WorkspaceExplorer({
  personal,
  personalEntries,
  search,
  activeEntryId,
  controller,
  onOpenFile,
  onNewSqlFile,
  onNewNotebook,
  onDeleted,
}: {
  personal: WorkspaceSpace;
  personalEntries: WorkspaceEntry[];
  search: string;
  activeEntryId: string | null;
  controller: ExplorerController;
  onOpenFile: (entry: WorkspaceEntry, space: WorkspaceSpace) => void;
  onNewSqlFile: (place: Place) => void;
  onNewNotebook: (place: Place) => void;
  onDeleted: (entryIds: string[]) => void;
}) {
  const queryClient = useQueryClient();
  const spacesQuery = useQuery({
    queryKey: ["workspace-spaces"],
    queryFn: workspaceApi.spaces,
    staleTime: 30_000,
  });
  const shared = useMemo(
    () => (spacesQuery.data?.spaces ?? []).filter((space) => space.kind === "shared"),
    [spacesQuery.data],
  );
  const sharedTrees = useQueries({
    queries: shared.map((space) => ({
      queryKey: ["workspace-space-tree", space.id],
      queryFn: () => workspaceApi.sharedTree(space.id),
      staleTime: 15_000,
    })),
  });

  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([rootKey(personal)]));
  const [selected, setSelected] = useState<string | null>(null);

  const spaceEntries = useMemo(() => {
    const map = new Map<string, WorkspaceEntry[]>();
    map.set(personal.id, personalEntries);
    shared.forEach((space, index) => {
      map.set(space.id, filterEntries(sharedTrees[index]?.data?.entries ?? [], search));
    });
    return map;
  }, [personal.id, personalEntries, shared, sharedTrees, search]);

  // While searching, open every folder so matches are visible.
  useEffect(() => {
    if (!search.trim()) return;
    setExpanded((current) => {
      const next = new Set(current);
      for (const [spaceId, entries] of spaceEntries) {
        next.add(`root:${spaceId}`);
        for (const entry of entries) {
          if (entry.entry_type === "folder") next.add(`${spaceId}:${entry.path}`);
        }
      }
      return next;
    });
  }, [search, spaceEntries]);

  const refresh = (space: WorkspaceSpace) =>
    queryClient.invalidateQueries({
      queryKey: space.kind === "personal" ? ["workspace-tree"] : ["workspace-space-tree", space.id],
    });

  const createItems = (place: Place): MenuItem[] => {
    const writable = canEditSpace(place.space);
    return [
      { kind: "item", label: "New SQL file", icon: FileCode2, disabled: !writable,
        onSelect: () => onNewSqlFile(place) },
      { kind: "item", label: "New notebook", icon: NotebookPen, disabled: !writable,
        onSelect: () => onNewNotebook(place) },
      { kind: "item", label: "New folder", icon: FolderPlus, disabled: !writable,
        onSelect: () => controller.open({ kind: "new-folder", place }) },
    ];
  };

  const rootItems = (space: WorkspaceSpace): MenuItem[] => {
    const items = createItems({ space, parentPath: "" });
    if (space.kind === "shared") {
      items.push({ kind: "separator" });
      if (space.permission === "owner") {
        items.push(
          { kind: "item", label: "Share with roles…", icon: Share2,
            onSelect: () => controller.open({ kind: "share", space }) },
          { kind: "item", label: "Rename workspace", icon: Pencil,
            onSelect: () => controller.open({ kind: "rename-workspace", space }) },
          { kind: "item", label: "Delete workspace", icon: Trash2, destructive: true,
            onSelect: () => controller.open({ kind: "delete-workspace", space }) },
        );
      } else {
        items.push({
          kind: "label",
          text: `Shared by ${space.owner}, ${space.permission === "edit" ? "can edit" : "can view"}`,
        });
      }
    }
    return items;
  };

  const entryItems = (space: WorkspaceSpace, entry: WorkspaceEntry): MenuItem[] => {
    const writable = canEditSpace(space);
    const changes: MenuItem[] = [
      { kind: "item", label: "Rename", icon: Pencil, disabled: !writable,
        onSelect: () => controller.open({ kind: "rename", space, entry }) },
      { kind: "item", label: "Move to…", icon: FolderInput, disabled: !writable,
        onSelect: () => controller.open({ kind: "move", space, entry }) },
      { kind: "item", label: "Delete", icon: Trash2, destructive: true, disabled: !writable,
        onSelect: () => controller.open({ kind: "delete", space, entry }) },
    ];
    if (entry.entry_type === "folder") {
      return [...createItems({ space, parentPath: entry.path }), { kind: "separator" }, ...changes];
    }
    return changes;
  };

  const renderEntries = (space: WorkspaceSpace, parentPath: string): ReactNode =>
    childrenOf(spaceEntries.get(space.id) ?? [], parentPath).map((entry) => {
      const key = entryKey(space, entry.path);
      if (entry.entry_type === "folder") {
        const items = entryItems(space, entry);
        return (
          <RowMenu key={entry.id} items={items}>
            <div>
              <FileTreeFolder
                path={key}
                name={entry.name}
                actions={<RowMenuButton items={items} label={`Actions for ${entry.name}`} />}
                rowProps={{
                  onFocus: () => controller.setPlace({ space, parentPath: entry.path }),
                }}
              >
                {renderEntries(space, entry.path)}
              </FileTreeFolder>
            </div>
          </RowMenu>
        );
      }
      const items = entryItems(space, entry);
      return (
        <RowMenu key={entry.id} items={items}>
          <div>
            <FileTreeFile
              path={key}
              name={entry.name}
              icon={entryIcon(entry)}
              className={entry.id === activeEntryId ? "[&>button]:bg-primary/10 [&>button]:text-primary" : undefined}
              actions={<RowMenuButton items={items} label={`Actions for ${entry.name}`} />}
              onOpen={() => {
                controller.setPlace({ space, parentPath: entry.parent_path });
                onOpenFile(entry, space);
              }}
            />
          </div>
        </RowMenu>
      );
    });

  const roots = [personal, ...shared];

  return (
    <>
      <FileTree
        aria-label="Workspaces"
        className="px-2 py-2"
        expanded={expanded}
        onExpandedChange={setExpanded}
        selectedPath={selected}
        onSelect={(path) => {
          setSelected(path);
          const space = roots.find((root) => rootKey(root) === path);
          if (space) controller.setPlace({ space, parentPath: "" });
        }}
      >
        {roots.map((space) => {
          const items = rootItems(space);
          const entries = spaceEntries.get(space.id) ?? [];
          return (
            <RowMenu key={space.id} items={items}>
              <div>
                <FileTreeFolder
                  path={rootKey(space)}
                  name={
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate font-medium">{space.name}</span>
                      {space.kind === "shared" && space.permission === "view" ? (
                        <span className="shrink-0 text-xs text-muted-foreground">View only</span>
                      ) : null}
                    </span>
                  }
                  icon={
                    <FileTreeIcon>{space.kind === "personal" ? <User /> : <Users />}</FileTreeIcon>
                  }
                  actions={<RowMenuButton items={items} label={`Actions for ${space.name}`} />}
                >
                  {entries.length ? (
                    renderEntries(space, "")
                  ) : (
                    <p className="py-1.5 pl-12 text-xs text-muted-foreground">
                      {search.trim() ? "No matching files" : "No files yet"}
                    </p>
                  )}
                </FileTreeFolder>
              </div>
            </RowMenu>
          );
        })}
      </FileTree>

      <ExplorerDialogs
        controller={controller}
        entriesOf={(space) =>
          space.kind === "personal"
            ? personalEntries
            : (sharedTrees[shared.findIndex((s) => s.id === space.id)]?.data?.entries ?? [])
        }
        refresh={refresh}
        onExpand={(key) => setExpanded((current) => new Set(current).add(key))}
        onDeleted={onDeleted}
      />
    </>
  );
}

function NameDialog({
  open,
  title,
  description,
  initial,
  action,
  onSubmit,
  onOpenChange,
}: {
  open: boolean;
  title: string;
  description: string;
  initial: string;
  action: string;
  onSubmit: (name: string) => Promise<void>;
  onOpenChange: (open: boolean) => void;
}) {
  const [name, setName] = useState(initial);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) setName(initial);
  }, [open, initial]);
  const submit = async () => {
    const clean = name.trim();
    if (!clean) return;
    setBusy(true);
    try {
      await onSubmit(clean);
      onOpenChange(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : `Could not ${action.toLowerCase()}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <Input
          autoFocus
          aria-label="Name"
          value={name}
          maxLength={120}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void submit();
          }}
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={busy || !name.trim()} onClick={() => void submit()}>
            {action}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ExplorerDialogs({
  controller,
  entriesOf,
  refresh,
  onExpand,
  onDeleted,
}: {
  controller: ExplorerController;
  entriesOf: (space: WorkspaceSpace) => WorkspaceEntry[];
  refresh: (space: WorkspaceSpace) => Promise<void>;
  onExpand: (key: string) => void;
  onDeleted: (entryIds: string[]) => void;
}) {
  const queryClient = useQueryClient();
  const { dialog, close } = controller;
  const onOpenChange = (open: boolean) => {
    if (!open) close();
  };
  const [target, setTarget] = useState<string>("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (dialog?.kind === "move") setTarget("");
  }, [dialog]);

  const spacesChanged = () => queryClient.invalidateQueries({ queryKey: ["workspace-spaces"] });

  return (
    <>
      <NameDialog
        open={dialog?.kind === "new-folder"}
        title="New folder"
        description={
          dialog?.kind === "new-folder"
            ? `In ${dialog.place.parentPath ? `${dialog.place.space.name} / ${dialog.place.parentPath}` : dialog.place.space.name}`
            : ""
        }
        initial="New folder"
        action="Create folder"
        onOpenChange={onOpenChange}
        onSubmit={async (name) => {
          if (dialog?.kind !== "new-folder") return;
          const { space, parentPath } = dialog.place;
          await workspaceApi.createFolder(space.id, parentPath, name);
          await refresh(space);
          onExpand(parentPath ? `${space.id}:${parentPath}` : `root:${space.id}`);
        }}
      />

      <NameDialog
        open={dialog?.kind === "new-workspace"}
        title="New shared workspace"
        description="A place for files you share with roles. You own it; next, choose who sees it."
        initial=""
        action="Create workspace"
        onOpenChange={onOpenChange}
        onSubmit={async (name) => {
          const { space } = await workspaceApi.createSpace(name);
          await spacesChanged();
          onExpand(`root:${space.id}`);
          // Straight on to sharing: a workspace no role sees is only the owner's.
          setTimeout(() => controller.open({ kind: "share", space }), 0);
        }}
      />

      <NameDialog
        open={dialog?.kind === "rename"}
        title={dialog?.kind === "rename" ? `Rename ${dialog.entry.name}` : "Rename"}
        description="Open tabs keep working under the new name."
        initial={dialog?.kind === "rename" ? dialog.entry.name : ""}
        action="Rename"
        onOpenChange={onOpenChange}
        onSubmit={async (name) => {
          if (dialog?.kind !== "rename") return;
          await workspaceApi.move(dialog.entry.id, name, dialog.entry.parent_path);
          await refresh(dialog.space);
        }}
      />

      <NameDialog
        open={dialog?.kind === "rename-workspace"}
        title="Rename workspace"
        description="Everyone it is shared with sees the new name."
        initial={dialog?.kind === "rename-workspace" ? dialog.space.name : ""}
        action="Rename"
        onOpenChange={onOpenChange}
        onSubmit={async (name) => {
          if (dialog?.kind !== "rename-workspace") return;
          await workspaceApi.renameSpace(dialog.space.id, name);
          await spacesChanged();
        }}
      />

      <Dialog open={dialog?.kind === "move"} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{dialog?.kind === "move" ? `Move ${dialog.entry.name}` : "Move"}</DialogTitle>
            <DialogDescription>
              {dialog?.kind === "move" ? `To another folder in ${dialog.space.name}` : ""}
            </DialogDescription>
          </DialogHeader>
          {dialog?.kind === "move" ? (
            <Select value={target} onValueChange={setTarget}>
              <SelectTrigger aria-label="Destination folder">
                <SelectValue placeholder="Choose a folder" />
              </SelectTrigger>
              <SelectContent>
                {moveTargets(entriesOf(dialog.space), dialog.entry).map((path) => (
                  <SelectItem key={path || "(top)"} value={path || "(top)"}>
                    {path || `${dialog.space.name} (top level)`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={close}>
              Cancel
            </Button>
            <Button
              disabled={!target || busy}
              onClick={async () => {
                if (dialog?.kind !== "move") return;
                setBusy(true);
                try {
                  const destination = target === "(top)" ? "" : target;
                  await workspaceApi.move(dialog.entry.id, dialog.entry.name, destination);
                  await refresh(dialog.space);
                  if (destination) onExpand(`${dialog.space.id}:${destination}`);
                  close();
                } catch (error) {
                  toast.error(error instanceof Error ? error.message : "Could not move it");
                } finally {
                  setBusy(false);
                }
              }}
            >
              Move
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={dialog?.kind === "delete"}
        onOpenChange={onOpenChange}
        destructive
        isLoading={busy}
        title={dialog?.kind === "delete" ? `Delete ${dialog.entry.name}?` : "Delete?"}
        desc={
          dialog?.kind === "delete"
            ? dialog.entry.entry_type === "folder"
              ? "The folder and everything in it are deleted."
              : dialog.space.kind === "shared"
                ? "It is deleted for everyone this workspace is shared with."
                : "The file is deleted."
            : ""
        }
        confirmText="Delete"
        handleConfirm={async () => {
          if (dialog?.kind !== "delete") return;
          setBusy(true);
          try {
            const removed = subtree(entriesOf(dialog.space), dialog.entry).map((e) => e.id);
            await workspaceApi.remove(dialog.entry.id);
            onDeleted(removed);
            await refresh(dialog.space);
            close();
          } catch (error) {
            toast.error(error instanceof Error ? error.message : "Could not delete it");
          } finally {
            setBusy(false);
          }
        }}
      />

      <ConfirmDialog
        open={dialog?.kind === "delete-workspace"}
        onOpenChange={onOpenChange}
        destructive
        isLoading={busy}
        title={dialog?.kind === "delete-workspace" ? `Delete ${dialog.space.name}?` : "Delete?"}
        desc="The workspace and all its files are deleted for everyone it is shared with."
        confirmText="Delete workspace"
        handleConfirm={async () => {
          if (dialog?.kind !== "delete-workspace") return;
          setBusy(true);
          try {
            const removed = entriesOf(dialog.space).map((e) => e.id);
            await workspaceApi.deleteSpace(dialog.space.id);
            onDeleted(removed);
            controller.setPlace({
              space: (await queryClient.fetchQuery({
                queryKey: ["workspace-spaces"],
                queryFn: workspaceApi.spaces,
              })).spaces[0],
              parentPath: "",
            });
            close();
          } catch (error) {
            toast.error(error instanceof Error ? error.message : "Could not delete the workspace");
          } finally {
            setBusy(false);
          }
        }}
      />

      <ShareDialog
        space={dialog?.kind === "share" ? dialog.space : null}
        onOpenChange={onOpenChange}
      />
    </>
  );
}
