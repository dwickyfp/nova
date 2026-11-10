import { ChevronDown, FileCode2, FolderPlus, NotebookPen, Plus, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { canEditSpace } from "./api";
import type { ExplorerController, Place } from "./controller";

/**
 * "SQL file" with a menu for the other new items. Everything is created
 * where the person last selected in the tree (a workspace and folder).
 */
export function NewItemButton({
  controller,
  onSqlFile,
  onNotebook,
}: {
  controller: ExplorerController;
  onSqlFile: (place: Place) => void;
  onNotebook: (place: Place) => void;
}) {
  const { place } = controller;
  const writable = canEditSpace(place.space);
  const where = place.parentPath
    ? `${place.space.name} / ${place.parentPath}`
    : place.space.name;
  return (
    <div className="flex w-full items-stretch" role="group" aria-label="Create">
      <Button
        size="sm"
        variant="outline"
        className="flex-1 justify-center gap-2 rounded-r-none"
        disabled={!writable}
        title={`New SQL file in ${where}`}
        onClick={() => onSqlFile(place)}
      >
        <Plus className="size-4" />
        SQL file
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            size="sm"
            variant="outline"
            className="-ml-px rounded-l-none px-2"
            aria-label="More things to create"
          >
            <ChevronDown className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-60">
          <DropdownMenuLabel className="truncate text-xs font-normal text-muted-foreground">
            In {where}
          </DropdownMenuLabel>
          <DropdownMenuItem disabled={!writable} onSelect={() => onSqlFile(place)}>
            <FileCode2 className="size-4" />
            SQL file
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!writable} onSelect={() => onNotebook(place)}>
            <NotebookPen className="size-4" />
            Notebook
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={!writable}
            onSelect={() => controller.open({ kind: "new-folder", place })}
          >
            <FolderPlus className="size-4" />
            Folder
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => controller.open({ kind: "new-workspace" })}>
            <Users className="size-4" />
            Shared workspace
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
