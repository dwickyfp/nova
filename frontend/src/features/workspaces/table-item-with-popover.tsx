import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { Clock, FileCode, Hash, Table2, Type } from "lucide-react";
import { api } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import { useIsMobile } from "@/hooks/use-mobile";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { QueryResponse } from "./types";

type ColumnInfo = { name: string; type: string };
const PREVIEW_LIMIT = 10;

const TablePopoverContext = createContext<{
  activeId: string | null;
  setActiveId: Dispatch<SetStateAction<string | null>>;
} | null>(null);

export function TablePopoverProvider({ children }: { children: ReactNode }) {
  const [activeId, setActiveId] = useState<string | null>(null);
  return (
    <TablePopoverContext.Provider value={{ activeId, setActiveId }}>
      {children}
    </TablePopoverContext.Provider>
  );
}

function quoteIdentifier(identifier: string) {
  return `\`${identifier.replace(/`/g, "``")}\``;
}

function ColumnTypeIcon({ type }: { type: string }) {
  if (/INT|FLOAT|DOUBLE|DECIMAL|NUMERIC|NUMBER/i.test(type))
    return <Hash className="size-3 shrink-0 text-info-strong" />;
  if (/DATE|TIME/i.test(type))
    return <Clock className="size-3 shrink-0 text-success-strong" />;
  if (/BOOL/i.test(type))
    return (
      <span className="flex size-3 shrink-0 items-center justify-center text-[9px] font-bold text-warning-strong">
        B
      </span>
    );
  return <Type className="size-3 shrink-0 text-primary" />;
}

export function TableItemWithPopover({
  name,
  database,
  schema,
}: {
  name: string;
  database: string;
  schema: string;
}) {
  const popovers = useContext(TablePopoverContext);
  if (!popovers)
    throw new Error("TableItemWithPopover requires TablePopoverProvider");
  const { activeId, setActiveId } = popovers;
  const id = useId();
  const open = activeId === id;
  const [tab, setTab] = useState("detail");
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const openedByHover = useRef(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const isMobile = useIsMobile();
  const user = useAuthStore((state) => state.auth.user);
  const identity = [user?.username, user?.activeRole];

  function cancelClose() {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  }

  function changeOpen(next: boolean) {
    cancelClose();
    if (next) {
      if (!open) setTab("detail");
      setActiveId(id);
    } else {
      setActiveId((current) => (current === id ? null : current));
    }
  }

  function scheduleClose() {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      setActiveId((current) => (current === id ? null : current));
    }, 200);
  }

  useEffect(
    () => () => {
      if (closeTimer.current) clearTimeout(closeTimer.current);
    },
    [open],
  );

  const columnsQuery = useQuery({
    queryKey: ["workspace-table-columns", database, schema, name, ...identity],
    queryFn: ({ signal }) =>
      api.get<{ columns: ColumnInfo[]; count: number }>(
        `/objects/databases/${encodeURIComponent(database)}/tables/${encodeURIComponent(name)}/columns`,
        signal,
      ),
    enabled: open,
    staleTime: 60_000,
  });
  const previewQuery = useQuery({
    queryKey: ["workspace-table-preview", database, schema, name, ...identity],
    queryFn: ({ signal }) =>
      api.post<QueryResponse[]>(
        "/query/execute",
        {
          sql: `SELECT * FROM ${quoteIdentifier(database)}.${quoteIdentifier(name)} LIMIT ${PREVIEW_LIMIT}`,
          database,
          schema,
          max_rows: PREVIEW_LIMIT,
        },
        signal,
      ),
    enabled: open && tab === "preview",
    staleTime: 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const preview = previewQuery.data?.[0];
  const previewFailed =
    previewQuery.isError || (previewQuery.isSuccess && !preview?.success);
  const columns = columnsQuery.data?.columns ?? [];

  return (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex w-full min-w-0 max-w-full items-center gap-2 rounded-md px-2 py-1 text-left text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
          onPointerEnter={(event) => {
            if (event.pointerType !== "mouse") return;
            openedByHover.current = true;
            changeOpen(true);
          }}
          onPointerLeave={(event) => {
            if (event.pointerType === "mouse") scheduleClose();
          }}
          onClick={(event) => {
            event.preventDefault();
            openedByHover.current = false;
            changeOpen(true);
            contentRef.current
              ?.querySelector<HTMLElement>('[role="tab"][data-state="active"]')
              ?.focus();
          }}
        >
          <Table2 className="size-3.5 shrink-0 text-primary" />
          <span className="min-w-0 flex-1 truncate">{name}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent
        ref={contentRef}
        aria-label={`${name} table details`}
        side={isMobile ? "bottom" : "right"}
        align="start"
        sideOffset={8}
        collisionPadding={8}
        className="z-[9999] flex h-76 w-64 max-w-[calc(100vw-1rem)] max-h-[var(--radix-popover-content-available-height)] animate-none! flex-col overflow-hidden p-0"
        onPointerEnter={cancelClose}
        onPointerLeave={(event) => {
          if (event.pointerType === "mouse") scheduleClose();
        }}
        onFocusCapture={cancelClose}
        onOpenAutoFocus={(event) => {
          if (openedByHover.current) event.preventDefault();
        }}
        onCloseAutoFocus={(event) => {
          if (openedByHover.current || (activeId !== null && activeId !== id))
            event.preventDefault();
        }}
      >
        <div className="flex max-h-1/3 shrink-0 items-start gap-1.5 overflow-y-auto border-b px-3 py-1.5 text-xs font-medium text-muted-foreground">
          <FileCode className="mt-0.5 size-3 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="whitespace-normal [overflow-wrap:anywhere]">
              {name}
            </div>
            {columnsQuery.data && (
              <div className="mt-0.5">{columnsQuery.data.count} columns</div>
            )}
          </div>
        </div>
        <Tabs
          value={tab}
          onValueChange={setTab}
          className="min-h-0 flex-1 gap-0"
        >
          <TabsList
            aria-label="Table information"
            indicatorVariant="underline"
            className="w-full shrink-0 rounded-none border-b bg-transparent p-0"
          >
            <TabsTrigger value="detail" className="rounded-none text-xs">
              Detail
            </TabsTrigger>
            <TabsTrigger value="preview" className="rounded-none text-xs">
              Preview Data
            </TabsTrigger>
          </TabsList>
          <TabsContent value="detail" className="min-h-0 overflow-auto py-1">
            {columnsQuery.isLoading && (
              <p
                role="status"
                className="px-3 py-2 text-xs text-muted-foreground"
              >
                Loading columns…
              </p>
            )}
            {columnsQuery.isError && (
              <p role="alert" className="px-3 py-2 text-xs text-destructive">
                Failed to load columns
              </p>
            )}
            {columnsQuery.isSuccess && !columns.length && (
              <p className="px-3 py-2 text-xs text-muted-foreground">
                No columns available.
              </p>
            )}
            <div className="grid min-w-full w-max grid-cols-[auto_1fr_auto] items-center gap-x-2 text-xs">
              {columns.map((column) => (
                <div
                  key={column.name}
                  className="col-span-3 grid grid-cols-subgrid items-center px-3 py-1"
                >
                  <ColumnTypeIcon type={column.type} />
                  <span className="whitespace-nowrap font-medium">
                    {column.name}
                  </span>
                  <span className="text-right whitespace-nowrap text-muted-foreground">
                    {column.type}
                  </span>
                </div>
              ))}
            </div>
          </TabsContent>
          <TabsContent value="preview" className="min-h-0 overflow-auto">
            {previewQuery.isLoading ? (
              <p
                role="status"
                className="px-3 py-2 text-xs text-muted-foreground"
              >
                Loading preview…
              </p>
            ) : previewFailed ? (
              <div className="px-3 py-2 text-xs">
                <p role="alert" className="text-destructive">
                  Could not load preview data.
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-1 text-xs"
                  onClick={() => void previewQuery.refetch()}
                >
                  Retry
                </Button>
              </div>
            ) : preview && preview.rows.length > 0 ? (
              <table
                aria-label={`Preview of ${name}, up to 10 rows`}
                className="w-max min-w-full border-collapse text-xs"
              >
                <thead className="sticky top-0 bg-popover">
                  <tr>
                    {preview.columns.map((column, index) => (
                      <th
                        key={index}
                        scope="col"
                        className="border-b px-3 py-1.5 text-left font-medium whitespace-nowrap"
                      >
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.slice(0, PREVIEW_LIMIT).map((row, rowIndex) => (
                    <tr key={rowIndex} className="border-b last:border-0">
                      {row.map((cell, index) => (
                        <td
                          key={index}
                          className="px-3 py-1.5 align-top whitespace-nowrap"
                        >
                          {cell == null ? (
                            <span className="text-muted-foreground italic">
                              NULL
                            </span>
                          ) : (
                            String(cell)
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="px-3 py-2 text-xs text-muted-foreground">
                No rows to preview.
              </p>
            )}
          </TabsContent>
        </Tabs>
      </PopoverContent>
    </Popover>
  );
}
