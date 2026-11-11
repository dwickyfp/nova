import {
  type ComponentProps,
  type CSSProperties,
  type Dispatch,
  type MouseEvent as ReactMouseEvent,
  type SetStateAction,
  memo,
  startTransition,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { format as formatSql } from "sql-formatter";
import Editor, { type Monaco } from "@monaco-editor/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import {
  BarChart3,
  Braces,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  Database,
  ListOrdered,
  Download,
  Folder,
  GripHorizontal,
  History,
  Play,
  RefreshCw,
  Square,
  Search,
  UserRoundCog,
  AlertTriangle,
} from "lucide-react";
import * as XLSX from "xlsx";
import { api } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/auth-store";
import { Header } from "@/components/layout/header";
import {
  defineNevaCapability,
  useAssistant,
  useNevaSurface,
} from "@/features/assistant";
import type { TurnContext } from "@/features/assistant";
import { createThread } from "@/features/assistant/thread-client";
import {
  applyCurrentRewrite,
  buildRewriteHunks,
  type AttachedQuery,
  type ProposedRewrite,
} from "@/features/assistant/query-attach";
import { ChartVisualization } from "./chart-visualization";
import { NewItemButton } from "./explorer/new-item-button";
import { PERSONAL, type WorkspaceSpace } from "./explorer/api";
import { useExplorerController, type Place } from "./explorer/controller";
import { freeName } from "./explorer/tree-data";
import { WorkspaceExplorer } from "./explorer/workspace-explorer";
import { notebookApi } from "./notebook/api";
import { isEditableName, isNotebookName, nextNotebookName } from "./notebook/names";
import { NOVA_TABLE_MIME } from "./notebook/helpers";
import { NotebookEditor } from "./notebook/notebook-editor";
import { DatabaseSchemaSelector } from "./database-schema-selector";
import { RewriteReview } from "./rewrite-review";
import {
  type CompletionResponse,
  buildStageCompletionPath,
  extractStageCompletionContext,
  formatStageCompletionDetail,
  getStageFileExtension,
  getStageCompletionInsertText,
  getStageCompletionKind,
  getStageCompletionReplacementRange,
  getStageCompletionSortText,
  shouldTriggerStageSuggestions,
} from "./stage-completion";
import { useTheme } from "@/context/theme-provider";
import { applyNovaSqlTheme } from "./monaco-theme";
import { FileHistoryDialog } from "./file-history-dialog";
import { WorkspaceTabStrip } from "./workspace-tab-strip";
import {
  QueryConfirmationDialog,
  snapshotQuery,
  type QuerySnapshot,
} from "./query-confirmation";
import {
  TableItemWithPopover,
  TablePopoverProvider,
} from "./table-item-with-popover";
import { explainPlanFromResult } from "./explain-plan";
import { roleSwitchFromResults } from "./session-role";
import {
  createStarterTemplateFile,
  getStarterTemplate,
} from "./starter-templates";
import { ExplainTreeView } from "./explain-tree";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { QueryHistory } from "./query-history";
import { splitSqlStatements, statementAtOffset } from "./sql-statements";
import {
  editorTextForNeva,
  replaceAttachedSql,
  safeNevaSql,
  summarizeQueryForNeva,
  type WorkspaceExecutionFeedback,
} from "./neva-feedback";
import type {
  HistoryResponse,
  QueryContextResponse,
  QueryResponse,
  SchemaResponse,
  SchemaTreeResponse,
  WorkspaceEntry,
  WorkspaceFileResponse,
  WorkspaceTabState,
  WorkspaceTreeResponse,
} from "./types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubItem,
} from "@/components/ui/sidebar";

type MonacoEditorInstance = Parameters<
  NonNullable<ComponentProps<typeof Editor>["onMount"]>
>[0];

const workspacesRoute = getRouteApi("/_authenticated/workspaces/");

let activeSqlEditorInstance: MonacoEditorInstance | null = null;
let activeSqlSelection: import("monaco-editor").Selection | null = null;

// Below this width the file panel and the editor no longer fit side by side,
// so the panel opens over the editor instead of pushing it to zero width.
const FILE_PANEL_OVERLAY_BELOW = 672;
// Wider bodies get the roomier file panel.
const FILE_PANEL_WIDE_FROM = 1024;
// The editor keeps at least this much height when the results panel is tall.
const EDITOR_MIN_HEIGHT = 160;

const NO_DATABASES: string[] = [];
/** Id prefix of SQL Neva suggests for the editor, as opposed to a repair. */
const NEVA_SUGGESTION = "neva-";

/**
 * A repair asks Neva to review every run; a suggestion only when its run
 * fails, since a run that worked needs no follow-up turn.
 */
function isNevaSuggestion(id: string): boolean {
  return id.startsWith(NEVA_SUGGESTION);
}
const NO_EXPANDED: Record<string, boolean> = {};
const NO_SCHEMAS: { name: string }[] = [];

function useElementSize(element: HTMLElement | null) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);
  return size;
}

function getSqlForExecution(fallbackSql: string) {
  const editor = activeSqlEditorInstance;
  const liveSelection = editor?.getSelection();
  const selection =
    liveSelection && !liveSelection.isEmpty()
      ? liveSelection
      : activeSqlSelection && !activeSqlSelection.isEmpty()
        ? activeSqlSelection
        : null;
  const selectedSql =
    editor && selection
      ? (editor.getModel()?.getValueInRange(selection).trim() ?? "")
      : "";

  if (selectedSql) return selectedSql;
  // Without a selection, run the statement at the cursor (the one just
  // written when the cursor sits after it), not the whole file.
  const model = editor?.getModel();
  const position = editor?.getPosition();
  const source = model ? model.getValue() : fallbackSql;
  const statements = splitSqlStatements(source);
  const offset =
    model && position ? model.getOffsetAt(position) : source.length;
  return statementAtOffset(statements, offset)?.text ?? fallbackSql.trim();
}

const SQL_KEYWORDS = [
  // Core DQL
  "SELECT",
  "FROM",
  "WHERE",
  "AND",
  "OR",
  "NOT",
  "IN",
  "NOT IN",
  "BETWEEN",
  "LIKE",
  "IS NULL",
  "IS NOT NULL",
  "AS",
  "DISTINCT",
  "ALL",
  "ANY",
  "EXISTS",
  "CASE",
  "WHEN",
  "THEN",
  "ELSE",
  "END",
  "CAST",
  "COALESCE",
  "NULLIF",
  "IF",
  "IFNULL",
  // Joins
  "JOIN",
  "INNER JOIN",
  "LEFT JOIN",
  "RIGHT JOIN",
  "FULL OUTER JOIN",
  "CROSS JOIN",
  "ON",
  "USING",
  // Aggregation & Grouping
  "GROUP BY",
  "HAVING",
  "ORDER BY",
  "ASC",
  "DESC",
  "LIMIT",
  "OFFSET",
  "WITH ROLLUP",
  "WITH",
  // Set Operations
  "UNION",
  "UNION ALL",
  "INTERSECT",
  "EXCEPT",
  "MINUS",
  // Subquery
  "LATERAL",
  "TABLESAMPLE",
  // Window Functions
  "OVER",
  "PARTITION BY",
  "ROWS",
  "RANGE",
  "UNBOUNDED PRECEDING",
  "UNBOUNDED FOLLOWING",
  "CURRENT ROW",
  // DML
  "INSERT INTO",
  "INSERT INTO ... VALUES",
  "INSERT INTO ... SELECT",
  "UPDATE",
  "DELETE FROM",
  "DELETE",
  "MERGE INTO",
  "VALUES",
  "SET",
  "ON DUPLICATE KEY UPDATE",
  "ON CONFLICT DO UPDATE",
  "ON CONFLICT DO NOTHING",
  // DDL
  "CREATE TABLE",
  "CREATE VIEW",
  "CREATE MATERIALIZED VIEW",
  "CREATE INDEX",
  "CREATE DATABASE",
  "CREATE FUNCTION",
  "CREATE EXTERNAL TABLE",
  "CREATE ROUTINE LOAD",
  "CREATE PIPE",
  "CREATE RESOURCE",
  "CREATE STORAGE VOLUME",
  "ALTER TABLE",
  "ALTER VIEW",
  "ALTER DATABASE",
  "DROP TABLE",
  "DROP VIEW",
  "DROP DATABASE",
  "DROP INDEX",
  "DROP MATERIALIZED VIEW",
  "DROP FUNCTION",
  "TRUNCATE TABLE",
  "RENAME TABLE",
  "ADD COLUMN",
  "DROP COLUMN",
  "MODIFY COLUMN",
  // Transaction & Session
  "BEGIN",
  "COMMIT",
  "ROLLBACK",
  "SAVEPOINT",
  "SET VARIABLE",
  "SET PROPERTY",
  "SET CATALOG",
  // Data Loading
  "LOAD LABEL",
  "CANCEL LOAD",
  "SHOW LOAD",
  "STREAM LOAD",
  "BROKER LOAD",
  "ROUTINE LOAD",
  "SHOW STREAM LOAD",
  "CANCEL STREAM LOAD",
  // Engine-specific
  "SHOW DATABASES",
  "SHOW TABLES",
  "SHOW COLUMNS",
  "SHOW CREATE TABLE",
  "SHOW PROCESSLIST",
  "SHOW VARIABLES",
  "SHOW BACKENDS",
  "SHOW FRONTENDS",
  "SHOW BROKER",
  "SHOW RESOURCES",
  "SHOW ROUTINE LOAD",
  "SHOW MATERIALIZED VIEWS",
  "SHOW PARTITIONS",
  "SHOW TABLET",
  "SHOW SNAPSHOT",
  "SHOW CATALOGS",
  "DESC",
  "DESCRIBE",
  "EXPLAIN",
  "EXPLAIN VERBOSE",
  "EXPLAIN COSTS",
  "EXPLAIN ANALYZE",
  "ANALYZE TABLE",
  "ANALYZE PROFILE",
  "KILL QUERY",
  "KILL CONNECTION",
  "GRANT",
  "REVOKE",
  "CREATE USER",
  "DROP USER",
  "ALTER USER",
  "CREATE ROLE",
  "DROP ROLE",
  "GRANT ROLE",
  "SHOW GRANTS",
  "SHOW ROLES",
  "ADMIN",
  "ADMIN SET",
  "ADMIN SHOW",
  "REFRESH",
  "REFRESH MATERIALIZED VIEW",
  "SUBMIT",
  "CANCEL",
  "RECOVER",
  "INSTALL",
  "UNINSTALL",
  "SHOW PLUGINS",
  // Aggregate Functions
  "COUNT",
  "SUM",
  "AVG",
  "MIN",
  "MAX",
  "COUNT(DISTINCT",
  "APPROX_COUNT_DISTINCT",
  "NDV",
  "GROUP_CONCAT",
  "BITMAP_UNION",
  "BITMAP_INTERSECT",
  "HLL_UNION",
  "HLL_CARDINALITY",
  "PERCENTILE_APPROX",
  "VARIANCE",
  "VAR_SAMP",
  "VAR_POP",
  "STDDEV",
  "STDDEV_SAMP",
  "STDDEV_POP",
  "ANY_VALUE",
  "BIT_AND",
  "BIT_OR",
  "BIT_XOR",
  // String Functions
  "CONCAT",
  "CONCAT_WS",
  "LENGTH",
  "CHAR_LENGTH",
  "LOWER",
  "UPPER",
  "LCASE",
  "UCASE",
  "LTRIM",
  "RTRIM",
  "TRIM",
  "LPAD",
  "RPAD",
  "SUBSTR",
  "SUBSTRING",
  "LEFT",
  "RIGHT",
  "REPLACE",
  "REVERSE",
  "REPEAT",
  "SPACE",
  "LOCATE",
  "INSTR",
  "POSITION",
  "HEX",
  "UNHEX",
  "ENCODE",
  "DECODE",
  "SPLIT",
  "SPLIT_PART",
  "REGEXP_REPLACE",
  "REGEXP_EXTRACT",
  "STR_TO_MAP",
  "PARSE_URL",
  "URL_ENCODE",
  "URL_DECODE",
  "CHAR",
  "ASCII",
  "FROM_BASE64",
  "TO_BASE64",
  "MONEY_FORMAT",
  "FORMAT",
  // Date/Time Functions
  "NOW",
  "CURDATE",
  "CURTIME",
  "CURRENT_DATE",
  "CURRENT_TIME",
  "CURRENT_TIMESTAMP",
  "DATE",
  "DATETIME",
  "TIMESTAMP",
  "DATE_ADD",
  "DATE_SUB",
  "DATE_DIFF",
  "DATEDIFF",
  "DATE_FORMAT",
  "DATE_TRUNC",
  "DATE_SLICE",
  "YEAR",
  "MONTH",
  "DAY",
  "HOUR",
  "MINUTE",
  "SECOND",
  "WEEK",
  "WEEKDAY",
  "DAYOFWEEK",
  "DAYOFYEAR",
  "QUARTER",
  "FROM_UNIXTIME",
  "UNIX_TIMESTAMP",
  "TO_DATE",
  "STR_TO_DATE",
  "TIME_TO_SEC",
  "SEC_TO_TIME",
  "MONTHS_ADD",
  "MONTHS_SUB",
  "YEARS_ADD",
  "YEARS_SUB",
  "HOURS_ADD",
  "HOURS_SUB",
  "MINUTES_ADD",
  "MINUTES_SUB",
  "SECONDS_ADD",
  "SECONDS_SUB",
  "MILLISECONDS_ADD",
  "LAST_DAY",
  "NEXT_DAY",
  "TIME_SLICE",
  "NOW",
  "UTC_TIMESTAMP",
  // Math Functions
  "ABS",
  "CEIL",
  "CEILING",
  "FLOOR",
  "ROUND",
  "TRUNCATE",
  "MOD",
  "POWER",
  "POW",
  "SQRT",
  "EXP",
  "LN",
  "LOG",
  "LOG2",
  "LOG10",
  "SIGN",
  "PI",
  "E",
  "RAND",
  "RANDOM",
  "SIN",
  "COS",
  "TAN",
  "ASIN",
  "ACOS",
  "ATAN",
  "ATAN2",
  "DEGREES",
  "RADIANS",
  "COT",
  "CONV",
  "BIN",
  "GREATEST",
  "LEAST",
  "WIDTH_BUCKET",
  "Pmod",
  // Conditional Functions
  "CASE WHEN",
  "IF",
  "IFNULL",
  "NULLIF",
  "COALESCE",
  "NVL",
  "NVL2",
  "DECODE",
  // JSON Functions
  "JSON_OBJECT",
  "JSON_ARRAY",
  "JSON_EXTRACT",
  "JSON_QUERY",
  "JSON_VALUE",
  "JSON_SET",
  "JSON_REPLACE",
  "JSON_REMOVE",
  "JSON_INSERT",
  "JSON_KEYS",
  "JSON_TYPE",
  "GET_JSON_STRING",
  "GET_JSON_INT",
  "GET_JSON_DOUBLE",
  "JSON_EACH",
  "PARSE_JSON",
  "TO_JSON",
  // Array Functions
  "ARRAY",
  "ARRAY_AGG",
  "ARRAY_CONCAT",
  "ARRAY_CONTAINS",
  "ARRAY_LENGTH",
  "ARRAY_SLICE",
  "ARRAY_DISTINCT",
  "ARRAY_SORT",
  "ARRAY_JOIN",
  "ARRAY_MAX",
  "ARRAY_MIN",
  "ARRAY_SUM",
  "ARRAY_AVG",
  "ARRAY_POSITION",
  "ARRAY_REMOVE",
  "ARRAY_MAP",
  "ARRAY_FILTER",
  "ARRAY_GENERATE",
  "ARRAY_TO_STRING",
  "ARRAYS_OVERLAP",
  "ARRAYS_INTERSECT",
  "CARDINALITY",
  // Map Functions
  "MAP",
  "MAP_KEYS",
  "MAP_VALUES",
  "MAP_CONCAT",
  "MAP_FROM_ARRAYS",
  "ELEMENT_AT",
  // Window Functions (named)
  "ROW_NUMBER",
  "RANK",
  "DENSE_RANK",
  "NTILE",
  "LAG",
  "LEAD",
  "FIRST_VALUE",
  "LAST_VALUE",
  "NTH_VALUE",
  "CUME_DIST",
  "PERCENT_RANK",
  // Bitmap Functions
  "BITMAP_EMPTY",
  "BITMAP_HASH",
  "BITMAP_HAS",
  "BITMAP_COUNT",
  "BITMAP_OR",
  "BITMAP_AND",
  "BITMAP_XOR",
  "BITMAP_NOT",
  "BITMAP_AGG",
  "TO_BITMAP",
  "BITMAP_FROM_STRING",
  "BITMAP_TO_STRING",
  "BITMAP_FROM_BINARY",
  "BITMAP_TO_BINARY",
  "BITMAP_FROM_ARRAY",
  "BITMAP_TO_ARRAY",
  "SUB_BITMAP",
  "BITMAP_MAX",
  "BITMAP_MIN",
  "BITMAP_ANDNOT",
  "BITMAP_SUBSET_LIMIT",
  "BITMAP_SUBSET_IN_RANGE",
  "BITMAP_REMOVE",
  // HLL Functions
  "HLL_EMPTY",
  "HLL_HASH",
  "HLL_UNION_AGG",
  "HLL_RAW_AGG",
  "HLL_MERGE",
  // Hash Functions
  "MD5",
  "MD5SUM",
  "MURMUR_HASH3_32",
  "MURMUR_HASH3_64",
  "XXHASH_32",
  "XXHASH_64",
  "SHA2",
  "SHA",
  // Utility
  "SLEEP",
  "UUID",
  "LAST_QUERY_ID",
  "CONNECTION_ID",
  "DATABASE",
  "SCHEMA",
  "VERSION",
  "CURRENT_USER",
  "SESSION_USER",
  "USER",
  // Table Function
  "UNNEST",
  "GENERATE",
  "FILES",
  // ML / AI functions
  "ML_PREDICT",
  "AI_COMPLETE",
];

export function WorkspacesPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const search = workspacesRoute.useSearch();
  // The database tree is built once, on first visit, and kept while hidden.
  const [databasesVisited, setDatabasesVisited] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<"workspaces" | "databases">(
    "workspaces",
  );
  const [secondaryCollapsed, setSecondaryCollapsed] = useState(false);
  // Overlay state for narrow bodies. Kept apart from secondaryCollapsed so a
  // narrow window never overwrites the persisted desktop preference.
  const [overlayPanelOpen, setOverlayPanelOpen] = useState(false);
  const [workspaceBody, setWorkspaceBody] = useState<HTMLDivElement | null>(
    null,
  );
  const [editorGrid, setEditorGrid] = useState<HTMLDivElement | null>(null);
  const workspaceBodySize = useElementSize(workspaceBody);
  const editorGridSize = useElementSize(editorGrid);
  const filePanelOverlay =
    workspaceBodySize.width > 0 &&
    workspaceBodySize.width < FILE_PANEL_OVERLAY_BELOW;
  const filePanelOpen = filePanelOverlay
    ? overlayPanelOpen
    : !secondaryCollapsed;
  const [workspaceSearch, setWorkspaceSearch] = useState("");
  const [databaseSearch, setDatabaseSearch] = useState("");
  const [tabs, setTabs] = useState<Record<string, WorkspaceTabState>>({});
  const [openTabIds, setOpenTabIds] = useState<string[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [expandedDatabases, setExpandedDatabases] = useState<
    Record<string, boolean>
  >({});
  const [expandedSchemas, setExpandedSchemas] = useState<
    Record<string, boolean>
  >({});
  const [schemasByDatabase, setSchemasByDatabase] = useState<
    Record<string, Array<{ name: string }>>
  >({});
  const [resultsHeight, setResultsHeight] = useState(350);
  const [resultsCollapsed, setResultsCollapsed] = useState(false);
  const [resultsTab, setResultsTab] = useState<"results" | "history" | "chart">(
    "results",
  );
  const [isResizingResults, setIsResizingResults] = useState(false);
  const [historyFilter, setHistoryFilter] = useState<"file" | "all">("file");
  // While Run all works through a script: which statement of how many.
  const [runProgress, setRunProgress] = useState<{
    current: number;
    total: number;
  } | null>(null);
  // The database a run left the session in (a USE moves it).
  const lastContextRef = useRef<{
    database: string | null;
    schema: string | null;
  } | null>(null);
  const stopRunAllRef = useRef(false);
  const [queryResults, setQueryResults] = useState<QueryResponse[] | null>(
    () => {
      try {
        const cached = sessionStorage.getItem("nova:last-query-results");
        if (cached) return JSON.parse(cached) as QueryResponse[];
        // Backward compat: try old single-result key
        const old = sessionStorage.getItem("nova:last-query-result");
        if (old) {
          const r = JSON.parse(old) as QueryResponse;
          sessionStorage.removeItem("nova:last-query-result");
          sessionStorage.setItem(
            "nova:last-query-results",
            JSON.stringify([r]),
          );
          return [r];
        }
        return null;
      } catch {
        return null;
      }
    },
  );
  const [lastExecutionByTab, setLastExecutionByTab] = useState<
    Record<string, WorkspaceExecutionFeedback>
  >({});
  const [activeResultIdx, setActiveResultIdx] = useState(0);
  const activeResult = queryResults?.[activeResultIdx] ?? null;
  /**
   * The Explain tab used to be a separate surface fed by a dedicated endpoint.
   * It is now folded into Results: an EXPLAIN run returns its plan as ordinary
   * rows, so the same panel decides how to render them. Detection reads the
   * statement the engine actually received (`original_sql`), not the editor
   * buffer, so re-running history entries behaves the same way.
   */
  const activeExplainPlan = activeResult
    ? explainPlanFromResult(activeResult)
    : null;
  const [running, setRunning] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const currentExecutionIdRef = useRef<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [pendingDestructiveSql, setPendingDestructiveSql] =
    useState<QuerySnapshot | null>(null);

  const activeTab = activeTabId ? tabs[activeTabId] : null;
  // The authenticated session is the single source of truth for the active role.
  // Everything the workspace does, including execution and schema discovery,
  // must run under this role, otherwise the UI can show one role while the
  // engine runs under another. Tab state keeps a role field for persistence and
  // read-only display, but it is always this value.
  //
  const activeRole = useAuthStore((state) => state.auth.user?.activeRole);
  const sessionRole = activeRole ?? "";
  const deferredWorkspaceSearch = useDeferredValue(workspaceSearch);
  const deferredDatabaseSearch = useDeferredValue(databaseSearch);
  const dragRef = useRef<{ startY: number; startHeight: number } | null>(null);
  // Short windows cap the results panel so the editor stays usable; the
  // stored height comes back once there is room again.
  const visibleResultsHeight =
    editorGridSize.height > 0
      ? Math.max(
          120,
          Math.min(resultsHeight, editorGridSize.height - EDITOR_MIN_HEIGHT),
        )
      : resultsHeight;

  useEffect(() => {
    if (!filePanelOverlay) setOverlayPanelOpen(false);
  }, [filePanelOverlay]);

  function toggleFilePanel() {
    if (filePanelOverlay) setOverlayPanelOpen((prev) => !prev);
    else setSecondaryCollapsed((prev) => !prev);
  }
  const saveTimerRef = useRef<number | null>(null);
  const stateSaveTimerRef = useRef<number | null>(null);
  const editorContentRef = useRef("");
  const conflictedFilesRef = useRef(new Set<string>());
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const activeTabIdRef = useRef(activeTabId);

  useEffect(() => {
    if (sidebarTab === "databases") setDatabasesVisited(true);
  }, [sidebarTab]);
  // Stable, so toggling one database re-renders that node, not all of them.
  const toggleDatabase = useCallback(
    (name: string) =>
      setExpandedDatabases((prev) => ({ ...prev, [name]: !prev[name] })),
    [],
  );
  const toggleSchema = useCallback(
    (key: string) =>
      setExpandedSchemas((prev) => ({ ...prev, [key]: !prev[key] })),
    [],
  );

  // Stable across keystrokes, so the memoized selector does not re-render
  // while only the SQL text changes.
  const selectDatabase = useCallback(
    async (value: string) => {
      if (!activeTabId) return;
      const schemas = await api.get<SchemaResponse>(
        `/objects/databases/${encodeURIComponent(value)}/schemas${sessionRole ? `?role=${encodeURIComponent(sessionRole)}` : ""}`,
      );
      setSchemasByDatabase((prev) => ({
        ...prev,
        [value]: schemas.schemas,
      }));
      setTabs((prev) => ({
        ...prev,
        [activeTabId]: {
          ...prev[activeTabId],
          database: value,
          schema: schemas.schemas[0]?.name ?? "default",
        },
      }));
    },
    [activeTabId, sessionRole],
  );
  const selectSchema = useCallback(
    (value: string) => {
      if (!activeTabId) return;
      setTabs((prev) => ({
        ...prev,
        [activeTabId]: {
          ...prev[activeTabId],
          schema: value,
        },
      }));
    },
    [activeTabId],
  );
  activeTabIdRef.current = activeTabId;
  const pendingRevealRef = useRef<{ tabId: string; sql: string } | null>(null);
  const handledTemplateRef = useRef<string | null>(null);

  const {
    open: assistantOpen,
    setOpen: setAssistantOpen,
    toggle: toggleAssistant,
    setBinding,
    attachQuery,
    proposedRewrite,
    setProposedRewrite,
    collapsedToPersist,
  } = useAssistant();
  const nevaCapabilities = useMemo(
    () => [
      defineNevaCapability({
        name: "tab.open",
        risk: "safe",
        argsSchema: z
          .object({ tab: z.enum(["results", "history", "chart"]) })
          .strict(),
        execute: ({ tab }) => {
          setResultsTab(tab);
          setResultsCollapsed(false);
        },
      }),
      defineNevaCapability({
        name: "editor.focus",
        risk: "safe",
        argsSchema: z.object({}).strict(),
        execute: () => activeSqlEditorInstance?.focus(),
      }),
      // Neva's SQL becomes a suggested change in the open file: the person
      // sees the diff and accepts or dismisses it. Nothing runs or saves here.
      defineNevaCapability({
        name: "editor.propose_sql",
        risk: "draft",
        argsSchema: z
          .object({
            sql: z.string().trim().min(1).max(20_000),
            mode: z.enum(["replace", "insert"]),
          })
          .strict(),
        execute: ({ sql, mode }) => {
          const tabId = activeTabIdRef.current;
          const tab = tabId ? tabsRef.current[tabId] : undefined;
          if (!tabId || !tab) throw new Error("Open a SQL file first.");
          const before = tab.content;
          const editor = activeSqlEditorInstance;
          const selection = editor?.getSelection();
          const model = editor?.getModel();
          // The file keeps its own ending, so a replace adds no blank line.
          const ending = before.endsWith("\n") ? "\n" : "";
          let next: string;
          if (
            mode === "replace" &&
            selection &&
            model &&
            !selection.isEmpty()
          ) {
            // A selection is what the person pointed at: only it changes.
            const start = model.getOffsetAt(selection.getStartPosition());
            const end = model.getOffsetAt(selection.getEndPosition());
            next = before.slice(0, start) + sql + before.slice(end);
          } else if (mode === "insert" && before.trim()) {
            next = `${before.trimEnd()}\n\n${sql}${ending}`;
          } else {
            next = `${sql}${ending}`;
          }
          if (next === before)
            throw new Error("The SQL is already in the editor.");
          const id = `${NEVA_SUGGESTION}${crypto.randomUUID()}`;
          setProposedRewrite({
            attachmentId: id,
            tabId,
            sourceContent: before,
            sql,
            hunks: buildRewriteHunks(before, next),
            sourceMessageId: id,
          });
        },
      }),
    ],
    [setProposedRewrite],
  );
  const { publishEvent: publishWorkspaceEvent, askNeva: askFromWorkspace } =
    useNevaSurface({
      id: "workspace.sql",
      route: "/workspaces",
      title: activeTab?.title ?? "SQL Workspace",
      context: () => {
        const editor = activeSqlEditorInstance;
        const selection = editor?.getSelection();
        const selectedText =
          selection && !selection.isEmpty()
            ? safeNevaSql(editor?.getModel()?.getValueInRange(selection) ?? "")
            : undefined;
        const cursor = editor?.getPosition();
        return {
          ...(activeTab
            ? {
                entity: {
                  type: "workspace_file",
                  id: activeTab.id,
                  name: activeTab.title,
                },
                editor: {
                  documentId: activeTab.id,
                  language: "sql",
                  selectedText,
                  // Sent as written, so the cursor's line matches the text.
                  text: editorTextForNeva(
                    editor?.getModel()?.getValue() ?? activeTab.content ?? "",
                  ),
                  cursor: cursor
                    ? { line: cursor.lineNumber, column: cursor.column }
                    : undefined,
                  dirty: activeTab.content !== activeTab.savedContent,
                },
              }
            : {}),
          ...(selectedText
            ? { selection: { type: "sql", text: selectedText } }
            : {}),
          execution:
            running && activeTab && currentExecutionIdRef.current
              ? {
                  type: "sql",
                  executionId: currentExecutionIdRef.current,
                  status: "running",
                }
              : activeTab
                ? lastExecutionByTab[activeTab.id]?.execution
                : undefined,
          view: {
            activeTab: resultsTab,
            filters: { historyScope: historyFilter, sidebar: sidebarTab },
          },
          domain: {
            database: activeTab?.database ?? null,
            schema: activeTab?.schema ?? null,
            role: sessionRole || null,
          },
        };
      },
      capabilities: activeTab ? nevaCapabilities : [],
      suggestedActions: activeTab
        ? [
            {
              label: "Explain this SQL",
              prompt: "Explain the SQL in the current editor.",
            },
            {
              label: "Check query performance",
              prompt: "Help me inspect the current query's performance.",
            },
            ...(lastExecutionByTab[activeTab.id]?.execution.status === "error"
              ? [
                  {
                    label: "Fix query error",
                    prompt:
                      "Diagnose and fix the last failed query in this workspace.",
                  },
                ]
              : []),
          ]
        : [
            {
              label: "Get started",
              prompt: "Help me write my first SQL query in Nova.",
            },
          ],
    });
  const threadsRef = useRef<Record<string, string>>({});
  const ensureThread = useCallback(async () => {
    if (!activeTabId) return null;
    const existing = threadsRef.current[activeTabId];
    if (existing) return existing;
    const thread = await createThread(activeTabId);
    threadsRef.current[activeTabId] = thread.thread_id;
    return thread.thread_id;
  }, [activeTabId]);

  const assistantContext = useMemo<TurnContext>(
    () => ({
      database: activeTab?.database ?? null,
      schema: activeTab?.schema ?? null,
      role: sessionRole || null,
    }),
    [activeTab?.database, activeTab?.schema, sessionRole],
  );

  const assistantOnError = useCallback(
    (message: string) => toast.error(message),
    [],
  );

  /**
   * Turns the assistant's proposed SQL into line hunks against the tab the
   * selection came from. Computed here (not in the provider) because the
   * editor's current text is the diff's "before" and only this surface has it.
   * If the referenced tab is gone the proposal is dropped.
   */
  const assistantOnProposedRewrite = useCallback(
    ({
      attachment,
      sql,
      messageId,
    }: {
      attachment: AttachedQuery;
      sql: string;
      messageId: string;
    }) => {
      const sourceTab = tabsRef.current[attachment.tabId];
      if (!sourceTab) return;
      const before = sourceTab.content;
      const next = replaceAttachedSql(
        before,
        attachment.sql,
        sql,
        attachment.startLine,
        attachment.endLine,
      );
      if (next === null) {
        toast.error(
          "The source SQL changed. Attach the current query to review a new fix.",
        );
        return;
      }
      setProposedRewrite({
        attachmentId: attachment.id,
        tabId: attachment.tabId,
        sourceContent: before,
        sql,
        hunks: buildRewriteHunks(before, next),
        sourceMessageId: messageId,
      });
    },
    [setProposedRewrite],
  );

  const assistantCanApproveUiAction = useCallback(
    ({ method, path }: { method: string; path: string }) => {
      if (!["PUT", "DELETE"].includes(method)) return true;
      const match = /^\/api\/v1\/workspaces\/files\/([^/]+)$/.exec(path);
      if (!match) return true;
      const tab = tabsRef.current[decodeURIComponent(match[1])];
      if (!tab || tab.content === tab.savedContent) return true;
      toast.error(
        "Save your unsaved SQL edits before approving this file action.",
      );
      return false;
    },
    [],
  );

  const assistantOnUiActionCompleted = useCallback(
    ({ method, path }: { method: string; path: string }) => {
      const match = /^\/api\/v1\/workspaces\/files\/([^/]+)$/.exec(path);
      if (!match) return;
      const id = decodeURIComponent(match[1]);
      if (method === "DELETE") {
        conflictedFilesRef.current.delete(id);
        setLastExecutionByTab((previous) => {
          const next = { ...previous };
          delete next[id];
          return next;
        });
        setTabs((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
        setOpenTabIds((prev) => prev.filter((tabId) => tabId !== id));
        if (activeTabIdRef.current === id) setActiveTabId(null);
        return;
      }
      if (method !== "PUT" || !tabsRef.current[id]) return;
      void api.get<WorkspaceFileResponse>(`/workspaces/files/${id}`).then(
        (file) => {
          const current = tabsRef.current[id];
          if (!current) return;
          if (current.content !== current.savedContent) {
            conflictedFilesRef.current.add(id);
            toast.error(
              "The SQL file changed while local edits were open. Copy your draft, then reload.",
            );
            return;
          }
          conflictedFilesRef.current.delete(id);
          if (activeTabIdRef.current === id)
            editorContentRef.current = file.content;
          setTabs((prev) => {
            const tab = prev[id];
            if (!tab || tab.content !== tab.savedContent) return prev;
            return {
              ...prev,
              [id]: {
                ...tab,
                title: file.entry.name,
                content: file.content,
                savedContent: file.content,
              },
            };
          });
        },
        () => {
          conflictedFilesRef.current.add(id);
          toast.error("The SQL file was saved, but its tab could not refresh.");
        },
      );
    },
    [],
  );

  // The global panel owns open/closed and the conversation; the workspace
  // supplies only the per-file binding (thread + active tab context). With no
  // file open the binding is cleared so the provider's global conversation
  // applies, instead of the workspace claiming the global key and inheriting a
  // closed file's transcript.
  const activeTabTitle = activeTab?.title;
  useEffect(() => {
    if (!activeTabId) {
      setBinding(null);
      return;
    }
    setBinding({
      key: activeTabId,
      context: assistantContext,
      ensureThread,
      onError: assistantOnError,
      onProposedRewrite: assistantOnProposedRewrite,
      canApproveUiAction: assistantCanApproveUiAction,
      onUiActionCompleted: assistantOnUiActionCompleted,
      title: activeTabTitle,
    });
    return () => setBinding(null);
  }, [
    activeTabId,
    activeTabTitle,
    assistantContext,
    ensureThread,
    assistantOnError,
    assistantOnProposedRewrite,
    assistantCanApproveUiAction,
    assistantOnUiActionCompleted,
    setBinding,
  ]);

  const workspaceTreeQuery = useQuery<WorkspaceTreeResponse>({
    queryKey: ["workspace-tree"],
    queryFn: () => api.get<WorkspaceTreeResponse>("/workspaces/tree"),
  });

  const username = useAuthStore((state) => state.auth.user?.username ?? "");
  const personalSpace = useMemo<WorkspaceSpace>(
    () =>
      workspaceTreeQuery.data?.space ?? {
        id: PERSONAL,
        name: "Personal Workspace",
        kind: "personal",
        owner: username,
        permission: "owner",
      },
    [workspaceTreeQuery.data?.space, username],
  );
  const explorer = useExplorerController(personalSpace);

  const queryContextQuery = useQuery<QueryContextResponse>({
    queryKey: ["query-context"],
    queryFn: () => api.get<QueryContextResponse>("/query/context"),
  });

  const databasesQuery = useQuery<{ databases: Array<{ name: string }> }>({
    queryKey: ["object-databases"],
    queryFn: () =>
      api.get<{ databases: Array<{ name: string }> }>("/objects/databases"),
  });

  const historyQuery = useQuery<HistoryResponse>({
    queryKey: ["query-history", historyFilter === "file" ? activeTabId : "all"],
    queryFn: () =>
      api.get<HistoryResponse>(
        `/query/history?limit=50${historyFilter === "file" && activeTabId ? `&file_id=${encodeURIComponent(activeTabId)}` : ""}`,
      ),
    enabled: resultsTab === "history",
    refetchInterval: resultsTab === "history" ? 5000 : false,
  });

  useEffect(() => {
    const tree = workspaceTreeQuery.data;
    const context = queryContextQuery.data;
    if (!tree || !context) return;
    setSecondaryCollapsed(tree.sidebar_collapsed);
    setOpenTabIds((prev) => (prev.length ? prev : tree.open_tabs));
    setActiveTabId(
      (prev) => prev ?? tree.active_tab ?? tree.open_tabs[0] ?? null,
    );

    if (!tree.open_tabs.length) return;
    setTabs((prev) => {
      if (Object.keys(prev).length) return prev;
      const next = { ...prev };
      for (const id of tree.open_tabs) {
        const entry = tree.entries.find((item) => item.id === id);
        if (!entry) continue;
        next[id] = {
          id,
          title: entry.name,
          content: "",
          savedContent: "",
          database:
            tree.defaults.database ??
            context.defaults.database ??
            context.databases[0] ??
            "",
          schema:
            tree.defaults.schema ??
            context.defaults.schema ??
            context.schemas[0] ??
            "default",
          role: sessionRole,
          loaded: false,
        };
      }
      // Saved tabs can name files that were deleted since; restoring none of
      // them must keep the same state object, not hand out a new empty one.
      return Object.keys(next).length ? next : prev;
    });
  }, [queryContextQuery.data, workspaceTreeQuery.data]);

  useEffect(() => {
    if (!activeTabId) return;
    const tab = tabs[activeTabId];
    if (!tab || tab.loaded) return;
    void openTab(activeTabId);
  }, [activeTabId, tabs]);

  useEffect(() => {
    const templateId = search.template;
    const template = templateId ? getStarterTemplate(templateId) : undefined;
    const tree = workspaceTreeQuery.data;
    const context = queryContextQuery.data;
    if (
      !template ||
      !tree ||
      !context ||
      handledTemplateRef.current === templateId
    )
      return;
    handledTemplateRef.current = templateId ?? null;

    void createStarterTemplateFile(template, tree.entries)
      .then((response) => {
        setOpenTabIds((prev) => [...new Set([...prev, response.entry.id])]);
        setActiveTabId(response.entry.id);
        setTabs((prev) => ({
          ...prev,
          [response.entry.id]: {
            id: response.entry.id,
            title: response.entry.name,
            content: response.content,
            savedContent: response.content,
            database:
              tree.defaults.database ??
              context.defaults.database ??
              context.databases[0] ??
              "",
            schema:
              tree.defaults.schema ?? context.defaults.schema ?? "default",
            role: sessionRole,
            loaded: true,
          },
        }));
        void queryClient.invalidateQueries({ queryKey: ["workspace-tree"] });
        void navigate({
          to: "/workspaces",
          search: { file: response.entry.id },
          replace: true,
        });
      })
      .catch((error) => {
        handledTemplateRef.current = null;
        toast.error(
          error instanceof Error
            ? error.message
            : "Failed to create template worksheet.",
        );
      });
  }, [
    search.template,
    workspaceTreeQuery.data,
    queryContextQuery.data,
    queryClient,
    navigate,
    sessionRole,
  ]);

  // Deep links from Home's Recent work: `?file=<id>` opens that worksheet and
  // `?q=<sql>` selects the matching statement so the user lands on the query,
  // not just the file. Runs once per distinct link; the pending reveal is
  // consumed after the editor mounts the file content.
  useEffect(() => {
    if (!(workspaceTreeQuery.data && queryContextQuery.data)) return;
    const fileId = search.file;
    if (!fileId) return;
    setOpenTabIds((prev) => (prev.includes(fileId) ? prev : [...prev, fileId]));
    setActiveTabId(fileId);
    if (search.q) {
      pendingRevealRef.current = { tabId: fileId, sql: search.q };
    }
  }, [search.file, search.q, workspaceTreeQuery.data, queryContextQuery.data]);

  useEffect(() => {
    const pending = pendingRevealRef.current;
    if (!pending) return;
    if (activeTabId !== pending.tabId) return;
    const tab = tabs[pending.tabId];
    if (!tab?.loaded) return;
    pendingRevealRef.current = null;
    const sql = pending.sql;
    const frame = window.requestAnimationFrame(() => {
      revealSqlSelection(tab.content, sql);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeTabId, tabs]);

  // Pre-load schemas when active tab's database changes
  useEffect(() => {
    if (!activeTab?.database || schemasByDatabase[activeTab.database]) return;
    void api
      .get<SchemaResponse>(
        `/objects/databases/${encodeURIComponent(activeTab.database)}/schemas${sessionRole ? `?role=${encodeURIComponent(sessionRole)}` : ""}`,
      )
      .then((response) => {
        setSchemasByDatabase((prev) => ({
          ...prev,
          [activeTab.database]: response.schemas,
        }));
      })
      .catch(() => {});
  }, [activeTab?.database, sessionRole]);

  useEffect(() => {
    if (!activeTab) return;
    if (!activeTab.loaded || activeTab.content === activeTab.savedContent)
      return;
    if (saveTimerRef.current) {
      window.clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = window.setTimeout(() => {
      void saveFile(
        activeTab.id,
        activeTab.content,
        activeTab.database,
        activeTab.schema,
        sessionRole,
      ).catch(() => {});
    }, 700);
    return () => {
      if (saveTimerRef.current) {
        window.clearTimeout(saveTimerRef.current);
      }
    };
  }, [activeTab]);

  // Persist last query result to sessionStorage for page refresh recovery
  useEffect(() => {
    if (queryResults) {
      try {
        sessionStorage.setItem(
          "nova:last-query-results",
          JSON.stringify(queryResults),
        );
      } catch {
        // Ignore quota errors for large result sets
      }
    }
  }, [queryResults]);

  useEffect(() => {
    const onMouseMove = (event: MouseEvent) => {
      if (!dragRef.current) return;
      const delta = dragRef.current.startY - event.clientY;
      setResultsHeight(Math.max(180, dragRef.current.startHeight + delta));
    };
    const onMouseUp = () => {
      dragRef.current = null;
      setIsResizingResults(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  useEffect(() => {
    const handleBeforeUnload = () => {
      if (!activeTab) return;
      if (activeTab.loaded && activeTab.content !== activeTab.savedContent) {
        void saveFile(
          activeTab.id,
          activeTab.content,
          activeTab.database,
          activeTab.schema,
          sessionRole,
        );
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [activeTab, sessionRole]);

  const filteredEntries = useMemo(() => {
    const entries = workspaceTreeQuery.data?.entries ?? [];
    if (!deferredWorkspaceSearch) return entries;
    const query = deferredWorkspaceSearch.toLowerCase();
    const matchedPaths = new Set<string>();
    for (const entry of entries) {
      if (!entry.path.toLowerCase().includes(query)) continue;
      matchedPaths.add(entry.path);
      let currentParent = entry.parent_path;
      while (currentParent) {
        matchedPaths.add(currentParent);
        const parentEntry = entries.find((item) => item.path === currentParent);
        currentParent = parentEntry?.parent_path ?? "";
      }
    }
    return entries.filter((entry) => matchedPaths.has(entry.path));
  }, [deferredWorkspaceSearch, workspaceTreeQuery.data?.entries]);

  const filteredDatabases = useMemo(() => {
    const databases = databasesQuery.data?.databases ?? [];
    if (!deferredDatabaseSearch) return databases;
    const query = deferredDatabaseSearch.toLowerCase();
    return databases.filter((db) => db.name.toLowerCase().includes(query));
  }, [databasesQuery.data?.databases, deferredDatabaseSearch]);

  const saveStateMutation = useMutation({
    mutationFn: (state: {
      open_tabs: string[];
      active_tab: string | null;
      sidebar_collapsed: boolean;
      assistant_collapsed: boolean;
      last_database: string | null;
      last_schema: string | null;
      last_role: string | null;
    }) => api.put<{ success: boolean }>("/workspaces/state", state),
  });

  useEffect(() => {
    if (!workspaceTreeQuery.data || !queryContextQuery.data) return;
    if (stateSaveTimerRef.current) {
      window.clearTimeout(stateSaveTimerRef.current);
    }
    stateSaveTimerRef.current = window.setTimeout(() => {
      void saveStateMutation.mutateAsync({
        open_tabs: openTabIds,
        active_tab: activeTabId,
        sidebar_collapsed: secondaryCollapsed,
        assistant_collapsed: collapsedToPersist(),
        last_database: activeTab?.database ?? null,
        last_schema: activeTab?.schema ?? null,
        last_role: sessionRole || null,
      });
    }, 300);
    return () => {
      if (stateSaveTimerRef.current) {
        window.clearTimeout(stateSaveTimerRef.current);
      }
    };
  }, [
    activeTab?.database,
    activeTab?.schema,
    activeTabId,
    assistantOpen,
    collapsedToPersist,
    openTabIds,
    queryContextQuery.data,
    secondaryCollapsed,
    sessionRole,
    workspaceTreeQuery.data,
  ]);

  async function openTab(id: string) {
    const entry = workspaceTreeQuery.data?.entries.find((item) => item.id === id);
    if (entry && isNotebookName(entry.name)) {
      // A notebook loads its own document; the SQL editor state stays empty
      // so the worksheet autosave never writes to the notebook file.
      setTabs((prev) => ({
        ...prev,
        [id]: {
          ...(prev[id] ?? { database: "", schema: "default", role: sessionRole }),
          id,
          title: entry.name,
          content: "",
          savedContent: "",
          loaded: true,
        } as WorkspaceTabState,
      }));
      return;
    }
    const file = await api.get<WorkspaceFileResponse>(
      `/workspaces/files/${id}`,
    );
    conflictedFilesRef.current.delete(id);
    const context = queryContextQuery.data;
    const defaults = workspaceTreeQuery.data?.defaults;
    editorContentRef.current = file.content;
    setTabs((prev) => ({
      ...prev,
      [id]: {
        id,
        title: file.entry.name,
        content: file.content,
        savedContent: file.content,
        database:
          prev[id]?.database ??
          defaults?.database ??
          context?.defaults.database ??
          context?.databases[0] ??
          "",
        schema:
          prev[id]?.schema ??
          defaults?.schema ??
          context?.defaults.schema ??
          "default",
        role: sessionRole,
        loaded: true,
        readOnly: file.space?.permission === "view",
      },
    }));
  }

  async function saveFile(
    id: string,
    content: string,
    database: string,
    schema: string,
    role: string,
  ) {
    if (tabs[id]?.readOnly) return false;
    if (conflictedFilesRef.current.has(id)) {
      toast.error(
        "Copy your local SQL edits, then reload this file before saving.",
      );
      return false;
    }
    const response = await api.put<WorkspaceFileResponse>(
      `/workspaces/files/${id}`,
      {
        content,
        database,
        schema,
        role,
      },
    );
    setTabs((prev) => ({
      ...prev,
      [id]: {
        ...prev[id],
        title: response.entry.name,
        savedContent: content,
      },
    }));
    await queryClient.invalidateQueries({ queryKey: ["workspace-tree"] });
    return true;
  }


  function entriesIn(space: WorkspaceSpace): WorkspaceEntry[] {
    if (space.kind === "personal") return workspaceTreeQuery.data?.entries ?? [];
    return (
      queryClient.getQueryData<WorkspaceTreeResponse>(["workspace-space-tree", space.id])
        ?.entries ?? []
    );
  }

  function refreshSpace(space: WorkspaceSpace) {
    return queryClient.invalidateQueries({
      queryKey:
        space.kind === "personal" ? ["workspace-tree"] : ["workspace-space-tree", space.id],
    });
  }

  async function createNewFile(place: Place = explorer.place) {
    const fileName = freeName(entriesIn(place.space), place.parentPath, "Untitled", ".sql");
    try {
      const response = await api.post<WorkspaceFileResponse>(
        "/workspaces/files",
        {
          name: fileName,
          parent_path: place.parentPath,
          content: "",
          space: place.space.id,
        },
      );
      await refreshSpace(place.space);
      startTransition(() => {
        setOpenTabIds((prev) => [...new Set([...prev, response.entry.id])]);
        setActiveTabId(response.entry.id);
        setTabs((prev) => ({
          ...prev,
          [response.entry.id]: {
            id: response.entry.id,
            title: response.entry.name,
            content: "",
            savedContent: "",
            database:
              activeTab?.database ??
              queryContextQuery.data?.defaults.database ??
              queryContextQuery.data?.databases[0] ??
              "",
            schema:
              activeTab?.schema ??
              queryContextQuery.data?.defaults.schema ??
              "default",
            role: sessionRole,
            loaded: true,
          },
        }));
      });
    } catch (error) {
      // Surface the backend's classified reason (e.g. storage unavailable)
      // instead of swallowing it — see NOVA-137.
      toast.error(
        error instanceof Error ? error.message : "Failed to create file.",
      );
    }
  }

  async function createNewNotebook(place: Place = explorer.place) {
    const name = nextNotebookName(
      entriesIn(place.space)
        .filter((entry) => entry.parent_path === place.parentPath)
        .map((entry) => entry.name),
    );
    try {
      const response = await notebookApi.create(
        name,
        place.parentPath,
        undefined,
        place.space.id,
      );
      await refreshSpace(place.space);
      startTransition(() => {
        setOpenTabIds((prev) => [...new Set([...prev, response.entry.id])]);
        setActiveTabId(response.entry.id);
        setTabs((prev) => ({
          ...prev,
          [response.entry.id]: {
            id: response.entry.id,
            title: response.entry.name,
            content: "",
            savedContent: "",
            database: activeTab?.database ?? "",
            schema: activeTab?.schema ?? "default",
            role: sessionRole,
            loaded: true,
          },
        }));
      });
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "The notebook could not be created",
      );
    }
  }

  /** Tabs of files that were deleted (with a folder or a workspace) close. */
  function closeDeletedTabs(entryIds: string[]) {
    const gone = new Set(entryIds);
    setLastExecutionByTab((previous) => {
      const next = { ...previous };
      for (const id of entryIds) delete next[id];
      return next;
    });
    setOpenTabIds((prev) => prev.filter((id) => !gone.has(id)));
    if (activeTabId && gone.has(activeTabId)) {
      setActiveTabId(openTabIds.find((id) => !gone.has(id)) ?? null);
    }
  }

  async function renameTabFile(
    tabId: string,
    newName: string,
  ): Promise<boolean> {
    const trimmed = newName.trim();
    if (!trimmed) return false;
    const tab = tabs[tabId];
    if (!tab) return false;
    if (trimmed === tab.title) return true;
    // Check uniqueness among open tabs
    const existingNames = Object.entries(tabs)
      .filter(([id]) => id !== tabId)
      .map(([, t]) => t.title.toLowerCase());
    if (existingNames.includes(trimmed.toLowerCase())) {
      toast.error(`A file named "${trimmed}" already exists.`);
      return false;
    }
    try {
      const response = await api.post<{ entry: WorkspaceEntry }>(
        "/workspaces/rename",
        { id: tabId, name: trimmed },
      );
      setTabs((prev) =>
        prev[tabId]
          ? { ...prev, [tabId]: { ...prev[tabId], title: response.entry.name } }
          : prev,
      );
      void queryClient.invalidateQueries({ queryKey: ["workspace-tree"] });
      return true;
    } catch {
      toast.error("Failed to rename file.");
      return false;
    }
  }

  function recordQueryFeedback(
    tabId: string,
    executionId: string,
    sql: string,
    results: QueryResponse[],
    elapsed: number,
    correlationId?: string,
  ): WorkspaceExecutionFeedback {
    const feedback = summarizeQueryForNeva(executionId, sql, results, elapsed);
    setLastExecutionByTab((previous) => ({ ...previous, [tabId]: feedback }));
    publishWorkspaceEvent({
      source: "execution",
      type: feedback.eventType,
      executionId,
      correlationId,
      status: feedback.eventType === "query_completed" ? "success" : "failure",
      payload: { ...feedback.eventPayload, documentId: tabId },
    });
    return feedback;
  }

  /**
   * A USE in the run moves the tab to that database, so the selector shows it
   * and the next run starts there instead of failing with "No database
   * selected".
   */
  function followSessionContext(tabId: string, response: QueryResponse[]) {
    const last = [...response]
      .reverse()
      .find(
        (result) => result.success && result.context_database !== undefined,
      );
    if (!last) return;
    const database = last.context_database ?? null;
    const schema = last.context_schema ?? null;
    lastContextRef.current = { database, schema };
    setTabs((previous) => {
      const tab = previous[tabId];
      if (!tab || (tab.database || null) === database) return previous;
      return {
        ...previous,
        [tabId]: {
          ...tab,
          database: database ?? "",
          schema: schema ?? "default",
        },
      };
    });
  }

  /** Runs every statement of the file one after another, stopping at the first failure. */
  async function runAll() {
    if (!activeTab) return;
    const statements = splitSqlStatements(
      editorContentRef.current || activeTab.content,
    );
    if (!statements.length) return;
    const tabId = activeTab.id;
    let context = {
      database: activeTab.database || null,
      schema: activeTab.schema || null,
    };
    setQueryResults(null);
    setActiveResultIdx(0);
    stopRunAllRef.current = false;
    try {
      for (const [index, statement] of statements.entries()) {
        if (stopRunAllRef.current) break;
        setRunProgress({ current: index + 1, total: statements.length });
        const outcome = await runQuery(
          false,
          statement.text,
          undefined,
          { tabId, ...context },
          { append: true },
        );
        if (outcome !== "success") break;
        context = lastContextRef.current ?? context;
      }
    } finally {
      setRunProgress(null);
    }
  }

  async function runQuery(
    confirmDestructive = false,
    sqlOverride?: string,
    correlationId?: string,
    snapshot?: {
      tabId: string;
      database: string | null;
      schema: string | null;
    },
    options: { append?: boolean } = {},
  ): Promise<
    "success" | "error" | "cancelled" | "pending-confirmation" | undefined
  > {
    const append = options.append ?? false;
    const showResults = (results: QueryResponse[]) => {
      if (!append) {
        setQueryResults(results);
        return;
      }
      setQueryResults((previous) => {
        const next = [...(previous ?? []), ...results];
        setActiveResultIdx(next.length - 1);
        return next;
      });
    };
    if (!activeTab) return;
    const sql =
      sqlOverride?.trim() ||
      getSqlForExecution(editorContentRef.current || activeTab.content);
    if (!sql) return;
    const requestSnapshot = snapshotQuery({
      sql,
      correlationId,
      ...(snapshot ?? {
        tabId: activeTab.id,
        database: activeTab.database || null,
        schema: activeTab.schema || null,
      }),
    });
    const executionId = crypto.randomUUID();
    currentExecutionIdRef.current = executionId;
    publishWorkspaceEvent({
      source: "execution",
      type: "query_started",
      executionId,
      correlationId,
      payload: { documentId: activeTab.id },
    });
    setRunning(true);
    lastContextRef.current = null;
    if (!append) {
      setQueryResults(null);
      setActiveResultIdx(0);
    }
    setElapsedMs(0);
    const controller = new AbortController();
    abortRef.current = controller;
    const startTime = Date.now();
    timerRef.current = setInterval(
      () => setElapsedMs(Date.now() - startTime),
      100,
    );
    try {
      const response = await api.post<QueryResponse[]>(
        "/query/execute",
        {
          sql,
          database: requestSnapshot.database,
          schema: requestSnapshot.schema,
          max_rows: 500,
          file_id: requestSnapshot.tabId,
          confirm_destructive: confirmDestructive,
        },
        controller.signal,
      );
      if (response.some((result) => result.needs_confirmation)) {
        if (activeTabIdRef.current === requestSnapshot.tabId) {
          setPendingDestructiveSql(requestSnapshot);
        } else {
          toast.error(
            "The active SQL file changed. Run the query again from that file.",
          );
        }
        return "pending-confirmation";
      }
      if (activeTabIdRef.current === requestSnapshot.tabId)
        showResults(response);
      followSessionContext(requestSnapshot.tabId, response);
      // A USE ROLE switched the whole session: show the new role everywhere,
      // and drop listings cached under the old one.
      const roleSwitch = roleSwitchFromResults(response);
      const signedIn = useAuthStore.getState().auth.user;
      if (roleSwitch && signedIn)
        useAuthStore.getState().auth.setUser({ ...signedIn, ...roleSwitch });
      const feedback = recordQueryFeedback(
        activeTab.id,
        executionId,
        sql,
        response,
        Date.now() - startTime,
        correlationId,
      );
      void queryClient.invalidateQueries({ queryKey: ["query-history"] });
      if (activeTab.content !== activeTab.savedContent) {
        try {
          await saveFile(
            activeTab.id,
            activeTab.content,
            activeTab.database,
            activeTab.schema,
            sessionRole,
          );
        } catch {
          toast.error("The query ran, but the SQL file could not be saved.");
        }
      }
      return feedback.execution.status === "success" ? "success" : "error";
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        showResults([
          {
            success: false,
            columns: [],
            rows: [],
            row_count: 0,
            affected_rows: 0,
            elapsed_ms: Date.now() - startTime,
            original_sql: sql,
            executed_sql: "",
            warnings: [
              `Query cancelled after ${((Date.now() - startTime) / 1000).toFixed(1)}s`,
            ],
            destructive: false,
            needs_confirmation: false,
          },
        ]);
        setLastExecutionByTab((previous) => {
          const next = { ...previous };
          delete next[activeTab.id];
          return next;
        });
        publishWorkspaceEvent({
          source: "execution",
          type: "query_cancelled",
          executionId,
          correlationId,
          payload: { documentId: activeTab.id },
        });
        return "cancelled";
      } else {
        const failedResult: QueryResponse = {
          success: false,
          columns: [],
          rows: [],
          row_count: 0,
          affected_rows: 0,
          elapsed_ms: 0,
          original_sql: sql,
          executed_sql: "",
          warnings: [error instanceof Error ? error.message : "Query failed"],
          destructive: false,
          needs_confirmation: false,
        };
        showResults([failedResult]);
        recordQueryFeedback(
          activeTab.id,
          executionId,
          sql,
          [failedResult],
          Date.now() - startTime,
          correlationId,
        );
        return "error";
      }
      void queryClient.invalidateQueries({ queryKey: ["query-history"] });
    } finally {
      if (timerRef.current) clearInterval(timerRef.current);
      abortRef.current = null;
      currentExecutionIdRef.current = null;
      setRunning(false);
    }
  }

  /**
   * Locates the statement referenced by a Home deep link inside the freshly
   * loaded worksheet and selects it, so the editor scrolls to and highlights
   * the query instead of dropping the cursor at line 1. Matching tolerates the
   * whitespace differences between the audited SQL and the saved file.
   */
  function revealSqlSelection(content: string, sql: string) {
    const editor = activeSqlEditorInstance;
    const model = editor?.getModel();
    if (!editor || !model) return;
    const needle = sql.trim();
    if (!needle) return;

    const haystack = content;
    let matchIndex = haystack.indexOf(needle);
    let matchLength = needle.length;

    if (matchIndex === -1) {
      // Whitespace-tolerant fallback: walk the source once, emitting the same
      // single-space collapse the needle uses, while remembering where each
      // emitted character came from. The first original index whose collapsed
      // stream matches the needle marks the selection start.
      const collapsedNeedle = needle.replace(/\s+/g, " ");
      const positions: number[] = [];
      let collapsedHaystack = "";
      for (let i = 0; i < haystack.length; i += 1) {
        const char = haystack[i];
        if (/\s/.test(char)) {
          if (collapsedHaystack.endsWith(" ")) continue;
          collapsedHaystack += " ";
        } else {
          collapsedHaystack += char;
        }
        positions.push(i);
      }
      const collapsedIndex = collapsedHaystack.indexOf(collapsedNeedle);
      if (collapsedIndex === -1) return;
      const startIndex = positions[collapsedIndex];
      const endIndex = positions[collapsedIndex + collapsedNeedle.length - 1];
      if (startIndex === undefined || endIndex === undefined) return;
      matchIndex = startIndex;
      matchLength = endIndex - startIndex + 1;
    }

    const start = model.getPositionAt(matchIndex);
    const end = model.getPositionAt(matchIndex + matchLength);
    editor.setSelection({
      startLineNumber: start.lineNumber,
      startColumn: start.column,
      endLineNumber: end.lineNumber,
      endColumn: end.column,
    });
    editor.revealRangeInCenter({
      startLineNumber: start.lineNumber,
      startColumn: start.column,
      endLineNumber: end.lineNumber,
      endColumn: end.column,
    });
    editor.focus();
  }

  /**
   * Attaches the editor's current selection (or the whole document when there
   * is none) to the assistant as a badge. The line range is 1-based inclusive
   * so the rewrite diff can locate it later.
   */
  function attachEditorSelectionToAssistant() {
    if (!activeTab) return;
    const editor = activeSqlEditorInstance;
    const model = editor?.getModel();
    const selection = editor?.getSelection();
    if (!editor || !model || !selection) return;
    const selectedSql = selection.isEmpty()
      ? ""
      : model.getValueInRange(selection);
    const rawSql = selectedSql || editor.getValue();
    const sql = rawSql.trim();
    if (!sql) {
      toast.error("Select a query to attach first");
      return;
    }
    const leading = rawSql.indexOf(sql);
    const startLine =
      (selection.isEmpty() ? 1 : selection.startLineNumber) +
      (rawSql.slice(0, leading).match(/\n/g)?.length ?? 0);
    const endLine = startLine + (sql.match(/\n/g)?.length ?? 0);
    attachQuery({
      sql,
      tabId: activeTab.id,
      fileName: activeTab.title,
      database: activeTab.database || null,
      schema: activeTab.schema || null,
      role: sessionRole || null,
      startLine,
      endLine,
    });
    if (!assistantOpen) toggleAssistant();
    toast.success("Query attached to Neva");
  }

  function askAboutWorkspaceQuery(prompt: string, sourceSql?: string) {
    if (!activeTab) return;
    const sql =
      sourceSql ??
      getSqlForExecution(editorContentRef.current || activeTab.content);
    const safeSql = safeNevaSql(sql);
    if (!safeSql) {
      void askFromWorkspace(prompt);
      return;
    }
    const content = activeTab.content;
    const model = activeSqlEditorInstance?.getModel();
    const liveSelection = activeSqlEditorInstance?.getSelection();
    const selection =
      liveSelection && !liveSelection.isEmpty()
        ? liveSelection
        : activeSqlSelection && !activeSqlSelection.isEmpty()
          ? activeSqlSelection
          : null;
    const selectedSql =
      selection && model ? model.getValueInRange(selection) : "";
    const selectedLine =
      selectedSql.trim() === safeSql && selection
        ? selection.startLineNumber +
          (selectedSql.slice(0, selectedSql.indexOf(safeSql)).match(/\n/g)
            ?.length ?? 0)
        : null;
    const index = content.indexOf(safeSql);
    const uniqueLine =
      index >= 0 && content.indexOf(safeSql, index + 1) === -1
        ? content.slice(0, index).split("\n").length
        : null;
    const startLine = selectedLine ?? uniqueLine;
    if (startLine === null) {
      void askFromWorkspace(prompt);
      return;
    }
    const endLine = startLine + safeSql.split("\n").length - 1;
    void askFromWorkspace(prompt, {
      sql: safeSql,
      tabId: activeTab.id,
      fileName: activeTab.title,
      database: activeTab.database || null,
      schema: activeTab.schema || null,
      role: sessionRole || null,
      startLine,
      endLine,
    });
  }

  async function approveProposedRewrite(runAfter = false) {
    const rewrite = proposedRewrite;
    if (!rewrite) return;
    const tab = tabs[rewrite.tabId];
    if (!tab) {
      setProposedRewrite(null);
      return;
    }
    const nextContent = applyCurrentRewrite(tab.content, rewrite);
    if (nextContent === null) {
      setProposedRewrite(null);
      toast.error(
        "The SQL changed after Neva proposed this rewrite. Request a new fix.",
      );
      return;
    }
    editorContentRef.current = nextContent;
    setTabs((prev) => ({
      ...prev,
      [rewrite.tabId]: { ...prev[rewrite.tabId], content: nextContent },
    }));
    setProposedRewrite(null);
    let saved = false;
    try {
      saved = await saveFile(
        rewrite.tabId,
        nextContent,
        tab.database,
        tab.schema,
        sessionRole,
      );
    } catch {
      toast.error(
        "The rewrite is in the editor, but the file could not be saved.",
      );
    }
    publishWorkspaceEvent({
      source: "assistant",
      type: "editor_patch_applied",
      artifactId: rewrite.sourceMessageId,
      correlationId: rewrite.sourceMessageId,
      status: "success",
      payload: { documentId: rewrite.tabId, persisted: saved },
    });
    if (saved)
      toast.success(
        runAfter ? "Rewrite applied. Running query…" : "Rewrite applied",
      );
    if (runAfter) {
      const outcome = await runQuery(
        false,
        rewrite.sql,
        rewrite.sourceMessageId,
      );
      if (
        outcome === "error" ||
        (outcome === "success" && !isNevaSuggestion(rewrite.sourceMessageId))
      ) {
        await askFromWorkspace(
          "Review the execution after the SQL rewrite. Report a verified fix only if the new query succeeded; if it failed, use the latest error to continue the repair.",
        );
      }
    }
  }

  function denyProposedRewrite() {
    if (proposedRewrite)
      publishWorkspaceEvent({
        source: "user",
        type: "editor_patch_rejected",
        artifactId: proposedRewrite.sourceMessageId,
        correlationId: proposedRewrite.sourceMessageId,
        payload: { documentId: proposedRewrite.tabId },
      });
    setProposedRewrite(null);
  }

  async function flushTabSave(tabId: string | null) {
    if (!tabId) return;
    if (conflictedFilesRef.current.has(tabId)) return;
    const tab = tabs[tabId];
    if (!tab || !tab.loaded || tab.content === tab.savedContent) return;
    await saveFile(tab.id, tab.content, tab.database, tab.schema, sessionRole);
  }

  function exportToExcel() {
    if (!queryResults?.length) return;
    const workbook = XLSX.utils.book_new();
    const baseName =
      activeTab?.title?.replace(/\.sql$/i, "") ?? "Query Results";
    queryResults.forEach((qr, idx) => {
      if (!qr.columns.length) return;
      const data = qr.rows.map((row) => {
        const obj: Record<string, unknown> = {};
        qr.columns.forEach((col, i) => {
          obj[col] = row[i];
        });
        return obj;
      });
      const worksheet = XLSX.utils.json_to_sheet(data);
      const sheetName =
        queryResults.length === 1 ? baseName.slice(0, 31) : `Result ${idx + 1}`;
      XLSX.utils.book_append_sheet(workbook, worksheet, sheetName);
    });
    const timestamp = new Date()
      .toISOString()
      .slice(0, 19)
      .replace(/[:T]/g, "-");
    XLSX.writeFile(workbook, `${baseName}_${timestamp}.xlsx`);
  }

  function activateTab(nextTabId: string) {
    void flushTabSave(activeTabId);
    setActiveTabId(nextTabId);
  }

  function closeTab(id: string) {
    void flushTabSave(id);
    setOpenTabIds((prev) => prev.filter((tabId) => tabId !== id));
    setTabs((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    if (activeTabId === id) {
      const nextId = openTabIds.find((tabId) => tabId !== id) ?? null;
      setActiveTabId(nextId);
    }
  }

  function reorderTabs(fromId: string, toId: string) {
    if (fromId === toId) return;
    setOpenTabIds((prev) => {
      const fromIndex = prev.indexOf(fromId);
      const toIndex = prev.indexOf(toId);
      if (fromIndex === -1 || toIndex === -1) return prev;
      const next = [...prev];
      next.splice(fromIndex, 1);
      next.splice(toIndex, 0, fromId);
      return next;
    });
  }

  function startResultsResize(event: ReactMouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    dragRef.current = {
      startY: event.clientY,
      startHeight: visibleResultsHeight,
    };
    setIsResizingResults(true);
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
  }

  return (
    <div data-layout="fixed" className="flex h-full min-h-0 flex-col">
      <Header fixed>
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-heading">Workspaces</h1>
            <p
              className="truncate text-sm text-muted-foreground"
              title="Write SQL against governed data, with each tab keeping its own role, database, and schema"
            >
              Write SQL against governed data, with each tab keeping its own
              role, database, and schema
            </p>
          </div>
        </div>
      </Header>

      <div
        ref={setWorkspaceBody}
        className="@container relative flex min-h-0 flex-1 overflow-hidden border-t"
      >
        <aside
          className={cn(
            "relative shrink-0 border-r bg-muted/20 transition-[width] duration-200",
            !filePanelOpen || filePanelOverlay
              ? "w-14"
              : workspaceBodySize.width >= FILE_PANEL_WIDE_FROM
                ? "w-80"
                : "w-72",
          )}
        >
          <div
            className={cn(
              "flex h-full min-h-0 flex-col",
              filePanelOverlay &&
                filePanelOpen &&
                "absolute inset-y-0 left-0 z-30 w-72 max-w-[calc(100cqw-1rem)] border-r bg-background shadow-lg",
            )}
            onKeyDown={(event) => {
              if (filePanelOverlay && event.key === "Escape") {
                setOverlayPanelOpen(false);
              }
            }}
          >
            <div className="border-b px-3 py-3">
              <div className="flex items-center justify-between gap-2">
                {filePanelOpen && (
                  <Tabs
                    value={sidebarTab}
                    onValueChange={(value) =>
                      setSidebarTab(value as "workspaces" | "databases")
                    }
                    className="w-full min-w-0"
                  >
                    <TabsList className="grid w-full min-w-0 grid-cols-2">
                      <TabsTrigger value="workspaces" className="min-w-0">
                        <span className="truncate">Workspaces</span>
                      </TabsTrigger>
                      <TabsTrigger value="databases" className="min-w-0">
                        <span className="truncate">Databases</span>
                      </TabsTrigger>
                    </TabsList>
                  </Tabs>
                )}
                <Button
                  variant="outline"
                  size="icon"
                  className="shrink-0"
                  aria-label={
                    filePanelOpen ? "Collapse file panel" : "Expand file panel"
                  }
                  aria-expanded={filePanelOpen}
                  onClick={toggleFilePanel}
                >
                  <ChevronRight
                    className={cn(
                      "size-4 transition-transform",
                      filePanelOpen && "rotate-180",
                    )}
                  />
                </Button>
              </div>
              {filePanelOpen && sidebarTab === "workspaces" && (
                <div className="mt-3">
                  <NewItemButton
                    controller={explorer}
                    onSqlFile={(place) => void createNewFile(place)}
                    onNotebook={(place) => void createNewNotebook(place)}
                  />
                </div>
              )}
            </div>

            {filePanelOpen && (
              <>
                <div className="border-b px-3 py-3">
                  <div className="flex items-center gap-1.5">
                    <div className="relative flex-1">
                      <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        value={
                          sidebarTab === "workspaces"
                            ? workspaceSearch
                            : databaseSearch
                        }
                        onChange={(event) =>
                          sidebarTab === "workspaces"
                            ? setWorkspaceSearch(event.target.value)
                            : setDatabaseSearch(event.target.value)
                        }
                        placeholder={
                          sidebarTab === "workspaces"
                            ? "Search files"
                            : "Search databases"
                        }
                        className="pl-9"
                      />
                    </div>
                    <button
                      type="button"
                      className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                      title="Refresh"
                      onClick={() => {
                        setRefreshing(true);
                        setTimeout(() => setRefreshing(false), 1000);
                        if (sidebarTab === "workspaces") {
                          void queryClient.invalidateQueries({
                            queryKey: ["workspace-tree"],
                          });
                        } else {
                          void queryClient.invalidateQueries({
                            queryKey: ["object-databases"],
                          });
                          void queryClient.invalidateQueries({
                            queryKey: ["db-schemas"],
                          });
                          void queryClient.invalidateQueries({
                            queryKey: ["schema-tree"],
                          });
                        }
                      }}
                    >
                      <RefreshCw
                        className={cn(
                          "size-3.5 transition-transform",
                          refreshing && "animate-spin",
                        )}
                      />
                    </button>
                  </div>
                </div>
                <ScrollArea className="min-h-0 min-w-0 flex-1 [&_[data-slot=scroll-area-viewport]>div]:block!">
                  <div hidden={sidebarTab !== "workspaces"}>
                    <WorkspaceExplorer
                      personal={personalSpace}
                      personalEntries={filteredEntries}
                      search={deferredWorkspaceSearch}
                      activeEntryId={activeTabId}
                      controller={explorer}
                      onNewSqlFile={(place) => void createNewFile(place)}
                      onNewNotebook={(place) => void createNewNotebook(place)}
                      onDeleted={closeDeletedTabs}
                      onOpenFile={(entry) => {
                        if (entry.entry_type !== "file") return;
                        if (!isEditableName(entry.name)) {
                          toast.message(`${entry.name} is a data file`, {
                            description:
                              "Read it from a notebook in the same folder, for example with read_csv or read_parquet",
                          });
                          return;
                        }
                        if (filePanelOverlay) setOverlayPanelOpen(false);
                        setOpenTabIds((prev) => [
                          ...new Set([...prev, entry.id]),
                        ]);
                        activateTab(entry.id);
                        setTabs((prev) => ({
                          ...prev,
                          [entry.id]:
                            prev[entry.id] ??
                            ({
                              id: entry.id,
                              title: entry.name,
                              content: "",
                              savedContent: "",
                              database:
                                activeTab?.database ??
                                queryContextQuery.data?.defaults.database ??
                                queryContextQuery.data?.databases[0] ??
                                "",
                              schema:
                                activeTab?.schema ??
                                queryContextQuery.data?.defaults.schema ??
                                "default",
                              role: sessionRole,
                              loaded: false,
                            } satisfies WorkspaceTabState),
                        }));
                      }}
                    />
                  </div>
                  {databasesVisited || sidebarTab === "databases" ? (
                    <div hidden={sidebarTab !== "databases"}>
                      <DatabaseExplorer
                        databases={filteredDatabases}
                        expandedDatabases={expandedDatabases}
                        expandedSchemas={expandedSchemas}
                        onToggleDatabase={toggleDatabase}
                        onToggleSchema={toggleSchema}
                        role={
                          sessionRole ||
                          queryContextQuery.data?.defaults.role ||
                          undefined
                        }
                        setSchemasByDatabase={setSchemasByDatabase}
                      />
                    </div>
                  ) : null}
                </ScrollArea>
              </>
            )}
          </div>
        </aside>

        {filePanelOverlay && filePanelOpen && (
          <button
            type="button"
            aria-label="Close file panel"
            className="absolute inset-0 z-20 bg-foreground/10"
            onClick={() => setOverlayPanelOpen(false)}
          />
        )}

        <section className="@container/editor flex min-h-0 min-w-0 flex-1 flex-col bg-background">
          <div className="flex items-end border-b border-border px-2 pt-2">
            <WorkspaceTabStrip
              tabs={tabs}
              openTabIds={openTabIds}
              activeTabId={activeTabId}
              onActivate={activateTab}
              onClose={closeTab}
              onReorder={reorderTabs}
              onRename={renameTabFile}
              onNewFile={() => void createNewFile()}
            />
          </div>

          {activeTab && isNotebookName(activeTab.title) ? (
            <NotebookEditor key={activeTab.id} notebookId={activeTab.id} />
          ) : activeTab ? (
            <>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
                <div className="flex shrink-0 items-center gap-2">
                  {running || runProgress ? (
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => {
                        stopRunAllRef.current = true;
                        abortRef.current?.abort();
                      }}
                    >
                      <Square className="size-4" />
                      Cancel {(elapsedMs / 1000).toFixed(1)}s
                    </Button>
                  ) : (
                    <div className="flex gap-px">
                      <Button
                        size="sm"
                        className="rounded-r-none"
                        title="Run the selection, or the statement at the cursor (Ctrl+Enter)"
                        onClick={() => runQuery()}
                      >
                        <Play className="size-4" />
                        Run
                      </Button>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            size="sm"
                            className="rounded-l-none px-2"
                            aria-label="More ways to run"
                          >
                            <ChevronDown className="size-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" className="w-56">
                          <DropdownMenuItem onSelect={() => void runAll()}>
                            <ListOrdered className="size-4" />
                            Run all
                            <DropdownMenuShortcut>⇧⌘↵</DropdownMenuShortcut>
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  )}
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-8 text-muted-foreground hover:text-foreground"
                    title="Format SQL (Ctrl+Shift+F)"
                    aria-label="Format SQL"
                    onClick={() => {
                      activeSqlEditorInstance
                        ?.getAction("editor.action.formatDocument")
                        ?.run();
                    }}
                  >
                    <Braces className="size-4" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-8 text-muted-foreground hover:text-foreground"
                    aria-label="Version history"
                    title="Version history"
                    onClick={() => setHistoryOpen(true)}
                  >
                    <History className="size-4" />
                  </Button>
                </div>
                <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
                  {/*
                    Read-only mirror of the session role. The active role is
                    chosen in the bottom-left account menu (Switch Role) and is
                    the single role the engine executes under, so it must not be
                    overridable per tab — that was how the UI could show one
                    role while queries ran as another.
                  */}
                  <span
                    className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm text-muted-foreground"
                    title="Active role — switch it from the account menu (bottom-left)"
                  >
                    <UserRoundCog className="size-3.5" />
                    {sessionRole || "No role"}
                  </span>
                  <DatabaseSchemaSelector
                    databases={
                      queryContextQuery.data?.databases ?? NO_DATABASES
                    }
                    selectedDatabase={activeTab.database}
                    schemas={
                      activeTab.database
                        ? (schemasByDatabase[activeTab.database] ?? NO_SCHEMAS)
                        : NO_SCHEMAS
                    }
                    selectedSchema={activeTab.schema}
                    onSelectDatabase={selectDatabase}
                    onSelectSchema={selectSchema}
                  />
                </div>
              </div>

              <div
                ref={setEditorGrid}
                className="grid min-h-0 flex-1 grid-rows-[1fr_auto] overflow-hidden"
              >
                <div className="min-h-0 overflow-hidden pt-3">
                  <MonacoSqlEditor
                    key={activeTab.id}
                    value={activeTab.content}
                    readOnly={activeTab.readOnly}
                    database={activeTab.database}
                    schema={activeTab.schema}
                    role={sessionRole}
                    onChange={(value) => {
                      const content = value ?? "";
                      editorContentRef.current = content;
                      setTabs((prev) => ({
                        ...prev,
                        [activeTab.id]: {
                          ...prev[activeTab.id],
                          content,
                        },
                      }));
                    }}
                    onRun={() => runQuery()}
                    onRunAll={() => void runAll()}
                    onAttachSelection={attachEditorSelectionToAssistant}
                    proposedRewrite={
                      proposedRewrite && proposedRewrite.tabId === activeTab.id
                        ? proposedRewrite
                        : null
                    }
                    onApproveRewrite={() => void approveProposedRewrite()}
                    onApproveAndRunRewrite={() =>
                      void approveProposedRewrite(true)
                    }
                    onDenyRewrite={denyProposedRewrite}
                  />
                </div>

                <div
                  style={
                    {
                      height: `${resultsCollapsed ? 44 : visibleResultsHeight}px`,
                    } satisfies CSSProperties
                  }
                  className={cn(
                    "mx-4 mt-2 flex shrink-0 flex-col overflow-hidden rounded-t-xl border bg-background",
                    isResizingResults
                      ? "transition-none"
                      : "transition-[height] duration-200 ease-in-out",
                  )}
                >
                  {!resultsCollapsed && (
                    <button
                      type="button"
                      aria-label="Resize results panel"
                      className="group flex h-3 w-full shrink-0 cursor-row-resize items-center justify-center bg-muted/15 hover:bg-muted/30"
                      onMouseDown={startResultsResize}
                    >
                      <span className="h-1 w-16 rounded-full bg-border transition-colors group-hover:bg-muted-foreground/40" />
                    </button>
                  )}
                  <div className="flex items-end border-b border-border px-2 pt-1">
                    <button
                      type="button"
                      className="mb-1.5 mr-1 rounded p-0.5 hover:bg-muted"
                      onClick={() => setResultsCollapsed((prev) => !prev)}
                    >
                      <ChevronDown
                        className={cn(
                          "size-3.5 transition-transform",
                          resultsCollapsed && "-rotate-90",
                        )}
                      />
                    </button>
                    <div className="flex items-end gap-0">
                      <button
                        type="button"
                        onClick={() => setResultsTab("results")}
                        className={cn(
                          "flex items-center gap-1.5 whitespace-nowrap rounded-t-md px-3 py-1 text-xs transition-colors",
                          resultsTab === "results"
                            ? "z-10 -mb-px border-x border-t-2 border-x-border border-t-primary border-b-0 bg-background text-primary"
                            : "border-b border-b-border text-muted-foreground hover:bg-muted/50",
                        )}
                      >
                        Results
                        {activeExplainPlan !== null && (
                          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
                            Explain
                          </span>
                        )}
                        {runProgress ? (
                          <span
                            role="status"
                            className="tabular-nums text-muted-foreground"
                          >
                            Running {runProgress.current} of {runProgress.total}{" "}
                            queries
                          </span>
                        ) : queryResults && queryResults.length > 0 ? (
                          <span className="tabular-nums text-muted-foreground">
                            {queryResults.length > 1
                              ? `${queryResults.length} queries · `
                              : ""}
                            {queryResults
                              .reduce((s, r) => s + r.row_count, 0)
                              .toLocaleString()}{" "}
                            rows ·{" "}
                            {queryResults
                              .reduce((s, r) => s + r.elapsed_ms, 0)
                              .toFixed(0)}{" "}
                            ms
                          </span>
                        ) : null}
                      </button>
                      <button
                        type="button"
                        onClick={() => setResultsTab("history")}
                        className={cn(
                          "flex items-center gap-1.5 rounded-t-md px-3 py-1 text-xs transition-colors",
                          resultsTab === "history"
                            ? "z-10 -mb-px border-x border-t-2 border-x-border border-t-primary border-b-0 bg-background text-primary"
                            : "border-b border-b-border text-muted-foreground hover:bg-muted/50",
                        )}
                      >
                        <Clock className="size-3" />
                        History
                      </button>
                      <button
                        type="button"
                        onClick={() => setResultsTab("chart")}
                        className={cn(
                          "flex items-center gap-1.5 rounded-t-md px-3 py-1 text-xs transition-colors",
                          resultsTab === "chart"
                            ? "z-10 -mb-px border-x border-t-2 border-x-border border-t-primary border-b-0 bg-background text-primary"
                            : "border-b border-b-border text-muted-foreground hover:bg-muted/50",
                        )}
                      >
                        <BarChart3 className="size-3" />
                        Chart
                      </button>
                    </div>
                    {!resultsCollapsed && (
                      <div className="ml-auto mb-1 flex items-center gap-1">
                        {resultsTab === "results" &&
                        activeResult?.columns.length ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="h-6 gap-1.5 px-2 text-xs"
                            onClick={exportToExcel}
                          >
                            <Download className="size-3" />
                            Export
                          </Button>
                        ) : null}
                        {resultsTab === "history" && (
                          <div className="flex items-center gap-1 text-xs">
                            <button
                              type="button"
                              className={cn(
                                "rounded-md px-2.5 py-0.5 transition-colors",
                                historyFilter === "file"
                                  ? "bg-primary text-primary-foreground"
                                  : "text-muted-foreground hover:bg-muted",
                              )}
                              onClick={() => setHistoryFilter("file")}
                            >
                              Current file
                            </button>
                            <button
                              type="button"
                              className={cn(
                                "rounded-md px-2.5 py-0.5 transition-colors",
                                historyFilter === "all"
                                  ? "bg-primary text-primary-foreground"
                                  : "text-muted-foreground hover:bg-muted",
                              )}
                              onClick={() => setHistoryFilter("all")}
                            >
                              All files
                            </button>
                          </div>
                        )}
                        <Button
                          type="button"
                          size="icon"
                          variant="ghost"
                          className="size-6"
                          onMouseDown={startResultsResize}
                        >
                          <GripHorizontal className="size-3.5" />
                        </Button>
                      </div>
                    )}
                  </div>
                  {!resultsCollapsed && resultsTab === "results" && (
                    <div className="flex min-h-0 flex-1 flex-col">
                      {activeResult &&
                        !activeResult.success &&
                        lastExecutionByTab[activeTab.id]?.execution.status ===
                          "error" && (
                          <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
                            <span className="mr-auto text-xs text-destructive">
                              Query failed
                            </span>
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                askAboutWorkspaceQuery(
                                  "Explain why the last query failed using its execution error.",
                                  activeResult.original_sql,
                                )
                              }
                            >
                              Explain
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              onClick={() =>
                                askAboutWorkspaceQuery(
                                  "Fix the last failed query. Propose a SQL rewrite for review; verify it only after I apply and run it.",
                                  activeResult.original_sql,
                                )
                              }
                            >
                              Fix with Neva
                            </Button>
                          </div>
                        )}
                      {/* Multi-result sub-tabs */}
                      {queryResults && queryResults.length > 1 && (
                        <div className="flex items-center gap-0.5 border-b border-border px-2 py-1">
                          {queryResults.map((_, idx) => (
                            <button
                              key={idx}
                              type="button"
                              onClick={() => setActiveResultIdx(idx)}
                              className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
                                activeResultIdx === idx
                                  ? "bg-primary/10 text-primary"
                                  : "text-muted-foreground hover:bg-muted/50"
                              }`}
                            >
                              {!queryResults[idx].success ? (
                                <AlertTriangle
                                  className="mr-1 inline size-3 align-[-2px] text-destructive"
                                  aria-label="Failed"
                                />
                              ) : null}
                              Result {idx + 1}
                            </button>
                          ))}
                        </div>
                      )}
                      <div className="min-h-0 flex-1 overflow-auto">
                        {activeExplainPlan !== null ? (
                          <div className="p-3">
                            <ExplainTreeView planText={activeExplainPlan} />
                          </div>
                        ) : (
                          <QueryResults queryResult={activeResult} />
                        )}
                      </div>
                    </div>
                  )}
                  {!resultsCollapsed && resultsTab === "history" && (
                    <QueryHistory
                      items={historyQuery.data?.items ?? []}
                      loading={historyQuery.isLoading}
                      onLoadSql={(sql) => {
                        if (!activeTab) return;
                        editorContentRef.current = sql;
                        setTabs((prev) => ({
                          ...prev,
                          [activeTab.id]: {
                            ...prev[activeTab.id],
                            content: sql,
                          },
                        }));
                      }}
                      onReRun={(sql) => {
                        if (!activeTab) return;
                        editorContentRef.current = sql;
                        setTabs((prev) => ({
                          ...prev,
                          [activeTab.id]: {
                            ...prev[activeTab.id],
                            content: sql,
                          },
                        }));
                        void runQuery(false, sql);
                      }}
                    />
                  )}
                  {!resultsCollapsed && resultsTab === "chart" && (
                    <ChartVisualization queryResult={activeResult} />
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center text-muted-foreground">
              Open or create a SQL file to start querying.
            </div>
          )}
        </section>
      </div>
      <FileHistoryDialog
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        entryId={activeTab?.id ?? null}
        fileName={activeTab?.title ?? ""}
      />
      <QueryConfirmationDialog
        pending={pendingDestructiveSql}
        onAssistantOpenChange={setAssistantOpen}
        onCancel={() => setPendingDestructiveSql(null)}
        onConfirm={(pending) => {
          setPendingDestructiveSql(null);
          if (pending && pending.tabId === activeTabId)
            void runQuery(
              true,
              pending.sql,
              pending.correlationId,
              pending,
            ).then((outcome) => {
              if (
                pending.correlationId &&
                (outcome === "error" ||
                  (outcome === "success" &&
                    !isNevaSuggestion(pending.correlationId)))
              )
                void askFromWorkspace(
                  "Review the execution after the SQL rewrite. Report a verified fix only if the new query succeeded; if it failed, use the latest error to continue the repair.",
                );
            });
          else
            toast.error(
              "The active SQL file changed. Run the query again from that file.",
            );
        }}
      />
    </div>
  );
}

const DatabaseExplorer = memo(function DatabaseExplorer({
  databases,
  expandedDatabases,
  expandedSchemas,
  onToggleDatabase,
  onToggleSchema,
  role,
  setSchemasByDatabase,
}: {
  databases: Array<{ name: string }>;
  expandedDatabases: Record<string, boolean>;
  expandedSchemas: Record<string, boolean>;
  onToggleDatabase: (name: string) => void;
  onToggleSchema: (key: string) => void;
  role?: string;
  setSchemasByDatabase: Dispatch<
    SetStateAction<Record<string, Array<{ name: string }>>>
  >;
}) {
  return (
    <TablePopoverProvider>
      <div className="px-2 py-3">
        <SidebarMenu>
          {databases.map((database) => (
            <DatabaseNode
              key={database.name}
              database={database.name}
              expanded={expandedDatabases[database.name] ?? false}
              // Closed databases get one constant, so opening a schema
              // re-renders only the database that holds it.
              expandedSchemas={
                expandedDatabases[database.name] ? expandedSchemas : NO_EXPANDED
              }
              onToggleDatabase={onToggleDatabase}
              onToggleSchema={onToggleSchema}
              role={role}
              setSchemasByDatabase={setSchemasByDatabase}
            />
          ))}
        </SidebarMenu>
      </div>
    </TablePopoverProvider>
  );
});

const DatabaseNode = memo(function DatabaseNode({
  database,
  expanded,
  expandedSchemas,
  onToggleDatabase,
  onToggleSchema,
  role,
  setSchemasByDatabase,
}: {
  database: string;
  expanded: boolean;
  expandedSchemas: Record<string, boolean>;
  onToggleDatabase: (name: string) => void;
  onToggleSchema: (key: string) => void;
  role?: string;
  setSchemasByDatabase: Dispatch<
    SetStateAction<Record<string, Array<{ name: string }>>>
  >;
}) {
  return (
    <Collapsible
      open={expanded}
      onOpenChange={() => onToggleDatabase(database)}
    >
      <SidebarMenuItem>
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted"
          >
            <ChevronRight
              className={cn(
                "size-4 shrink-0 transition-transform duration-200",
                expanded && "rotate-90",
              )}
            />
            <Database className="size-4 shrink-0 text-primary" />
            <span className="truncate font-medium">{database}</span>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          {expanded ? (
            <DatabaseSchemas
              database={database}
              expandedSchemas={expandedSchemas}
              onToggleSchema={onToggleSchema}
              role={role}
              setSchemasByDatabase={setSchemasByDatabase}
            />
          ) : null}
        </CollapsibleContent>
      </SidebarMenuItem>
    </Collapsible>
  );
});

/**
 * An open database's schemas. Its query exists only while the database is
 * open, instead of one idle query per listed database.
 */
function DatabaseSchemas({
  database,
  expandedSchemas,
  onToggleSchema,
  role,
  setSchemasByDatabase,
}: {
  database: string;
  expandedSchemas: Record<string, boolean>;
  onToggleSchema: (key: string) => void;
  role?: string;
  setSchemasByDatabase: Dispatch<
    SetStateAction<Record<string, Array<{ name: string }>>>
  >;
}) {
  const schemasQuery = useQuery<SchemaResponse>({
    queryKey: ["db-schemas", database, role],
    queryFn: () =>
      api.get<SchemaResponse>(
        `/objects/databases/${encodeURIComponent(database)}/schemas${role ? `?role=${encodeURIComponent(role)}` : ""}`,
      ),
  });

  useEffect(() => {
    if (schemasQuery.data) {
      setSchemasByDatabase((prev) =>
        prev[database] === schemasQuery.data.schemas
          ? prev
          : { ...prev, [database]: schemasQuery.data.schemas },
      );
    }
  }, [database, schemasQuery.data, setSchemasByDatabase]);

  return (
    <SidebarMenuSub>
      {schemasQuery.data?.schemas.map((schema) => (
        <SchemaNode
          key={`${database}:${schema.name}`}
          database={database}
          schema={schema.name}
          expanded={expandedSchemas[`${database}:${schema.name}`] ?? false}
          onToggleSchema={onToggleSchema}
          role={role}
        />
      ))}
    </SidebarMenuSub>
  );
}

const SchemaNode = memo(function SchemaNode({
  database,
  schema,
  expanded,
  onToggleSchema,
  role,
}: {
  database: string;
  schema: string;
  expanded: boolean;
  onToggleSchema: (key: string) => void;
  role?: string;
}) {
  const schemaKey = `${database}:${schema}`;

  return (
    <Collapsible open={expanded} onOpenChange={() => onToggleSchema(schemaKey)}>
      <SidebarMenuItem>
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted"
          >
            <ChevronRight
              className={cn(
                "size-4 shrink-0 transition-transform duration-200",
                expanded && "rotate-90",
              )}
            />
            <Folder className="size-4 shrink-0 text-muted-foreground" />
            <span className="truncate">{schema}</span>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          {expanded ? (
            <SchemaObjects database={database} schema={schema} role={role} />
          ) : null}
        </CollapsibleContent>
      </SidebarMenuItem>
    </Collapsible>
  );
});

/** An open schema's objects; queried only while the schema is open. */
function SchemaObjects({
  database,
  schema,
  role,
}: {
  database: string;
  schema: string;
  role?: string;
}) {
  const treeQuery = useQuery<SchemaTreeResponse>({
    queryKey: ["schema-tree", database, schema, role],
    queryFn: () =>
      api.get<SchemaTreeResponse>(
        `/objects/databases/${encodeURIComponent(database)}/schemas/${encodeURIComponent(schema)}/tree${role ? `?role=${encodeURIComponent(role)}` : ""}`,
      ),
  });
  if (!treeQuery.data) return null;
  return (
    <SidebarMenuSub className="space-y-2 py-1">
      <ObjectGroup
        title="Tables"
        items={treeQuery.data.tables}
        database={database}
        schema={schema}
      />
      <ObjectGroup
        title="Views"
        items={treeQuery.data.views}
        database={database}
        schema={schema}
      />
      <ObjectGroup
        title="Materialized Views"
        items={treeQuery.data.materialized_views}
        database={database}
        schema={schema}
      />
      <ObjectGroup
        title="Stages"
        items={treeQuery.data.stages}
        database={database}
        schema={schema}
      />
    </SidebarMenuSub>
  );
}

function ObjectGroup({
  title,
  items,
  database,
  schema,
}: {
  title: string;
  items: Array<{ name: string }>;
  database: string;
  schema: string;
}) {
  if (!items.length) return null;
  const isTables = title === "Tables";
  return (
    <div>
      <div className="px-2 py-0.5 text-xs font-medium text-muted-foreground">
        {title}
      </div>
      <div className="space-y-0.5">
        {items.map((item) => (
          <SidebarMenuSubItem
            key={item.name}
            draggable
            title="Drag into a notebook to add a preview query"
            onDragStart={(event) => {
              // A notebook turns this into a preview query; nothing is copied.
              event.dataTransfer.effectAllowed = "copy";
              event.dataTransfer.setData(
                NOVA_TABLE_MIME,
                JSON.stringify({ database, schema, name: item.name }),
              );
              event.dataTransfer.setData(
                "text/plain",
                [database, schema, item.name].filter(Boolean).join("."),
              );
            }}
          >
            {isTables ? (
              <TableItemWithPopover
                name={item.name}
                database={database}
                schema={schema}
              />
            ) : (
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-muted"
              >
                <span className="size-3.5 shrink-0 rounded-sm bg-muted-foreground/20" />
                <span className="truncate">{item.name}</span>
              </button>
            )}
          </SidebarMenuSubItem>
        ))}
      </div>
    </div>
  );
}

// Memoized: the page re-renders on every keystroke in the editor, and a large
// result or chart must not be redrawn when only the SQL text changed.
const QueryResults = memo(function QueryResults({
  queryResult,
}: {
  queryResult: QueryResponse | null;
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(100);
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [ctxMenu, setCtxMenu] = useState<{
    x: number;
    y: number;
    rowIdx: number;
    colIdx: number;
  } | null>(null);

  // Close context menu on click outside
  useEffect(() => {
    if (!ctxMenu) return;
    const close = () => setCtxMenu(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [ctxMenu]);

  function copyToClipboard(text: string) {
    navigator.clipboard.writeText(text);
    toast.success("Copied to clipboard");
  }

  function handleCellContext(
    e: React.MouseEvent,
    rowIdx: number,
    colIdx: number,
  ) {
    e.preventDefault();
    e.stopPropagation();
    setCtxMenu({ x: e.clientX, y: e.clientY, rowIdx, colIdx });
  }

  // Reset pagination on new results
  const prevRowCount = useRef(0);
  useEffect(() => {
    if (queryResult && queryResult.row_count !== prevRowCount.current) {
      setPage(1);
      setSortCol(null);
      prevRowCount.current = queryResult.row_count;
    }
  }, [queryResult]);

  if (!queryResult) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        Run a query to inspect table output here.
      </div>
    );
  }

  if (!queryResult.success) {
    const errors = [queryResult.error, ...(queryResult.warnings ?? [])].filter(
      Boolean,
    );
    return (
      <div className="p-4 text-sm text-destructive">
        {(errors.length ? errors : ["Query failed."]).map((message, i) => (
          <div key={i}>{message}</div>
        ))}
      </div>
    );
  }

  if (!queryResult.columns.length) {
    return (
      <div className="p-4 text-sm">
        Query completed. Affected rows: {queryResult.affected_rows}
      </div>
    );
  }

  // Sort rows
  const sortedRows =
    sortCol !== null
      ? [...queryResult.rows].sort((a, b) => {
          const av = a[sortCol],
            bv = b[sortCol];
          if (av === null && bv === null) return 0;
          if (av === null) return 1;
          if (bv === null) return -1;
          const cmp = av < bv ? -1 : av > bv ? 1 : 0;
          return sortDir === "asc" ? cmp : -cmp;
        })
      : queryResult.rows;

  // Paginate
  const totalPages = Math.max(1, Math.ceil(sortedRows.length / pageSize));
  const startIdx = (page - 1) * pageSize;
  const pageRows = sortedRows.slice(startIdx, startIdx + pageSize);

  // Warnings indicator (shown alongside data, not replacing it)
  const warningsBanner = queryResult.warnings?.length ? (
    <div className="border-b border-border bg-warning/10 px-4 py-2 text-xs text-warning-strong">
      {queryResult.warnings.map((w, i) => (
        <div key={i}>{w}</div>
      ))}
    </div>
  ) : null;
  const truncatedBanner = queryResult.truncated ? (
    <div className="border-b border-border bg-muted/40 px-4 py-2 text-xs text-muted-foreground">
      Showing the first {queryResult.row_count.toLocaleString()} rows. The
      query returned more; add a filter or LIMIT to see the rest.
    </div>
  ) : null;

  function handleSort(colIdx: number) {
    if (sortCol === colIdx) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(colIdx);
      setSortDir("asc");
    }
    setPage(1);
  }

  return (
    <div className="flex h-full flex-col">
      {warningsBanner}
      {truncatedBanner}
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-separate border-spacing-0 text-sm">
          <thead className="sticky top-0 z-10 bg-background">
            <tr>
              <th className="w-10 border-b border-r border-border px-1 py-1.5 text-center text-xs text-muted-foreground font-normal">
                #
              </th>
              {queryResult.columns.map((column, colIndex) => {
                const sample = queryResult.rows[0]?.[colIndex];
                const typeIcon =
                  typeof sample === "number"
                    ? "#"
                    : typeof sample === "boolean"
                      ? "⊙"
                      : typeof sample === "string"
                        ? String(sample).match(/^\d{4}-\d{2}-\d{2}/)
                          ? "◷"
                          : String(sample).match(/^[\d.,]+$/)
                            ? "#"
                            : "A"
                        : sample === null
                          ? "∅"
                          : "?";
                const isSorted = sortCol === colIndex;
                return (
                  <th
                    key={column}
                    className="cursor-pointer select-none border-b border-r border-border px-2 py-1.5 text-left font-normal whitespace-nowrap hover:bg-muted/50"
                    onClick={() => handleSort(colIndex)}
                  >
                    <span className="mr-1 text-muted-foreground">
                      {typeIcon}
                    </span>
                    {column}
                    {isSorted && (
                      <span className="ml-1 text-primary">
                        {sortDir === "asc" ? "↑" : "↓"}
                      </span>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row, rowIndex) => (
              <tr key={rowIndex} className="hover:bg-muted/30">
                <td className="border-b border-r border-border px-1 py-1 text-center text-xs text-muted-foreground">
                  {startIdx + rowIndex + 1}
                </td>
                {row.map((cell, cellIndex) => (
                  <td
                    key={`${rowIndex}-${cellIndex}`}
                    className="border-b border-r border-border px-2 py-1 whitespace-nowrap"
                    onContextMenu={(e) =>
                      handleCellContext(e, startIdx + rowIndex, cellIndex)
                    }
                  >
                    {cell === null ? (
                      <span className="font-mono text-xs text-muted-foreground">
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
      </div>
      {/* Pagination footer */}
      <div className="flex items-center justify-between border-t border-border px-3 py-1.5 text-xs text-muted-foreground">
        <span>
          Rows {startIdx + 1}–{Math.min(startIdx + pageSize, sortedRows.length)}{" "}
          of {sortedRows.length}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="rounded px-1.5 py-0.5 hover:bg-muted disabled:opacity-40"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            ◀ Prev
          </button>
          <span>
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            className="rounded px-1.5 py-0.5 hover:bg-muted disabled:opacity-40"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next ▶
          </button>
          <select
            className="rounded border border-border bg-background px-1.5 py-0.5 text-xs"
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value));
              setPage(1);
            }}
          >
            <option value={25}>25 / page</option>
            <option value={50}>50 / page</option>
            <option value={100}>100 / page</option>
            <option value={250}>250 / page</option>
          </select>
        </div>
      </div>
      {/* Right-click context menu */}
      {ctxMenu &&
        queryResult &&
        (() => {
          const { rowIdx, colIdx, x, y } = ctxMenu;
          const allRows = sortedRows;
          const cellVal = allRows[rowIdx]?.[colIdx];
          const row = allRows[rowIdx];
          const col = allRows.map((r) => r[colIdx]);
          const colName = queryResult.columns[colIdx];
          return (
            <div
              className="fixed z-[9999] min-w-[180px] rounded-md border border-border bg-popover p-1 shadow-lg"
              style={{ left: x, top: y }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-1 truncate border-b border-border px-2 py-1 text-xs text-muted-foreground">
                {colName} — Row {rowIdx + 1}
              </div>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent"
                onClick={() => {
                  copyToClipboard(cellVal === null ? "NULL" : String(cellVal));
                  setCtxMenu(null);
                }}
              >
                <Copy className="size-3.5" /> Copy cell value
              </button>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent"
                onClick={() => {
                  copyToClipboard(
                    row
                      .map((c) => (c === null ? "NULL" : String(c)))
                      .join("\t"),
                  );
                  setCtxMenu(null);
                }}
              >
                <Copy className="size-3.5" /> Copy row (TSV)
              </button>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent"
                onClick={() => {
                  copyToClipboard(
                    col
                      .map((c) => (c === null ? "NULL" : String(c)))
                      .join("\n"),
                  );
                  setCtxMenu(null);
                }}
              >
                <Copy className="size-3.5" /> Copy column ({colName})
              </button>
              <div className="my-1 border-t border-border" />
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent"
                onClick={() => {
                  const header = queryResult.columns.join("\t");
                  const body = allRows.map((r) =>
                    r.map((c) => (c === null ? "" : String(c))).join("\t"),
                  );
                  copyToClipboard([header, ...body].join("\n"));
                  setCtxMenu(null);
                }}
              >
                <Copy className="size-3.5" /> Copy all (CSV)
              </button>
            </div>
          );
        })()}
    </div>
  );
});

// ── Chart Visualization ─────────────────────────────────────────────
// Categorical series palette, read from tokens so dark mode stays correct.
// Literal calls keep each read inside the gate's A2 exemption.

function MonacoSqlEditor({
  value,
  readOnly = false,
  database,
  schema,
  role,
  onChange,
  onRun,
  onRunAll,
  onAttachSelection,
  proposedRewrite,
  onApproveRewrite,
  onApproveAndRunRewrite,
  onDenyRewrite,
}: {
  value: string;
  readOnly?: boolean;
  database: string;
  schema: string;
  role: string;
  onChange: (value?: string) => void;
  onRun: () => void;
  onRunAll: () => void;
  onAttachSelection: () => void;
  proposedRewrite: ProposedRewrite | null;
  onApproveRewrite: () => void;
  onApproveAndRunRewrite: () => void;
  onDenyRewrite: () => void;
}) {
  const providerRef = useRef<{ dispose(): void } | null>(null);
  const completionRequestRef = useRef<{
    id: number;
    controller: AbortController;
  } | null>(null);
  const onRunRef = useRef(onRun);
  onRunRef.current = onRun;
  const onRunAllRef = useRef(onRunAll);
  onRunAllRef.current = onRunAll;
  const onAttachSelectionRef = useRef(onAttachSelection);
  onAttachSelectionRef.current = onAttachSelection;
  // Refs for completion provider — prevents stale closure (provider registered once at mount)
  const valueRef = useRef(value);
  valueRef.current = value;
  const databaseRef = useRef(database);
  databaseRef.current = database;
  const schemaRef = useRef(schema);
  schemaRef.current = schema;
  const roleRef = useRef(role);
  roleRef.current = role;
  const { resolvedTheme } = useTheme();

  // The mounted editor + its monaco namespace, kept so the diff effect and the
  // context-menu action created at mount can reach them without a re-mount.
  const editorRef = useRef<MonacoEditorInstance | null>(null);
  const selectionListenerRef = useRef<{ dispose(): void } | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  // Flips once `onMount` has handed us the namespace, so the theme effect below
  // also runs on the first paint instead of only on later theme toggles.
  const [monacoReady, setMonacoReady] = useState(false);

  useEffect(() => {
    return () => {
      completionRequestRef.current?.controller.abort();
      providerRef.current?.dispose();
      selectionListenerRef.current?.dispose();
      selectionListenerRef.current = null;
      if (activeSqlEditorInstance) {
        activeSqlEditorInstance = null;
        activeSqlSelection = null;
      }
    };
  }, []);

  useEffect(() => {
    completionRequestRef.current?.controller.abort();
    completionRequestRef.current = null;
  }, [database, schema]);

  // Re-define and re-select the Nova SQL theme on first mount and whenever the
  // resolved theme changes. `onMount` runs once, so without this the editor
  // keeps whatever was selected at mount and a later light/dark toggle never
  // repaints it.
  useEffect(() => {
    const monaco = monacoRef.current;
    if (!monaco) return;
    const frame = requestAnimationFrame(() => {
      applyNovaSqlTheme(monaco, resolvedTheme === "dark");
    });
    return () => cancelAnimationFrame(frame);
  }, [resolvedTheme, monacoReady]);

  /**
   * Paints the proposed rewrite into the editor: replaced original lines in
   * red, proposed lines in green, inserted as whole-line decorations with a
   * margin marker so an empty proposed line still shows. Partial-line rewrites
   * naturally produce single-line hunks, so only the changed lines light up.
   */
  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (!editor || !monaco) return;

    const collection = editor.createDecorationsCollection();
    if (!proposedRewrite) {
      collection.clear();
      return () => collection.clear();
    }

    const decorations: import("monaco-editor").editor.IModelDeltaDecoration[] =
      [];
    for (const hunk of proposedRewrite.hunks) {
      // An insert has no original lines to mark red; its proposed lines are
      // shown in the overlay. Replace/delete light up the original range.
      if (hunk.kind === "insert") continue;
      decorations.push({
        range: new monaco.Range(hunk.startLine, 1, hunk.endLine, 1),
        options: {
          isWholeLine: true,
          className: "nova-rewrite-old-line",
          linesDecorationsClassName: "nova-rewrite-old-gutter",
        },
      });
    }

    collection.set(decorations);
    return () => collection.clear();
  }, [proposedRewrite]);

  // Colour tokens for the diff decorations live here because Monaco injects
  // them outside the module CSS scope.
  useEffect(() => {
    const styleId = "nova-rewrite-diff-styles";
    if (document.getElementById(styleId)) return;
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      .nova-rewrite-old-line { background: color-mix(in srgb, var(--destructive) 14%, transparent); }
      .nova-rewrite-old-gutter { border-left: 3px solid var(--destructive); }
      .nova-rewrite-new-line { background: color-mix(in srgb, var(--success) 14%, transparent); }
      .nova-rewrite-new-gutter { border-left: 3px solid var(--success); }
    `;
    document.head.appendChild(style);
  }, []);

  async function provideCompletions(
    textUntilPosition: string,
    signal?: AbortSignal,
  ): Promise<CompletionResponse["items"]> {
    const curValue = valueRef.current;
    const curDb = databaseRef.current;
    const curSchema = schemaRef.current;
    const curRole = roleRef.current;

    // @stage context is exclusive: never mix stage suggestions with SQL
    // relations, keywords, or Monaco word-based suggestions.
    const stageContext = extractStageCompletionContext(textUntilPosition);
    if (stageContext) {
      const response = await api.get<CompletionResponse>(
        buildStageCompletionPath(curDb, stageContext),
        signal,
      );
      return response.items;
    }

    const relationContext = extractRelationCompletionContext(textUntilPosition);
    if (relationContext?.type === "database") {
      const [databases, objects] = await Promise.all([
        api.get<CompletionResponse>(
          `/query/completions?kind=database&role=${encodeURIComponent(curRole)}&prefix=${encodeURIComponent(relationContext.prefix)}`,
          signal,
        ),
        api.get<CompletionResponse>(
          `/query/completions?kind=object&database=${encodeURIComponent(curDb)}&schema=${encodeURIComponent(curSchema)}&role=${encodeURIComponent(curRole)}&prefix=${encodeURIComponent(relationContext.prefix)}`,
          signal,
        ),
      ]);
      return dedupeCompletionItems([...databases.items, ...objects.items]);
    }

    if (relationContext?.type === "schema") {
      const response = await api.get<CompletionResponse>(
        `/query/completions?kind=schema&database=${encodeURIComponent(relationContext.database)}&role=${encodeURIComponent(curRole)}&prefix=${encodeURIComponent(relationContext.prefix)}`,
        signal,
      );
      return response.items;
    }

    if (relationContext?.type === "object") {
      const response = await api.get<CompletionResponse>(
        `/query/completions?kind=object&database=${encodeURIComponent(relationContext.database)}&schema=${encodeURIComponent(relationContext.schema)}&role=${encodeURIComponent(curRole)}&prefix=${encodeURIComponent(relationContext.prefix)}`,
        signal,
      );
      return response.items;
    }

    const aliasMatch = textUntilPosition.match(/([A-Za-z_][\w]*)\.$/);
    if (aliasMatch) {
      const tableName = resolveAliasTable(curValue, aliasMatch[1]);
      if (tableName) {
        const response = await api.get<CompletionResponse>(
          `/query/completions?kind=column&database=${encodeURIComponent(curDb)}&role=${encodeURIComponent(curRole)}&table=${encodeURIComponent(tableName)}`,
          signal,
        );
        return response.items;
      }
    }

    const objectMatch = textUntilPosition.match(
      /\b(FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z0-9_]*)$/i,
    );
    if (objectMatch) {
      const response = await api.get<CompletionResponse>(
        `/query/completions?kind=object&database=${encodeURIComponent(curDb)}&role=${encodeURIComponent(curRole)}&prefix=${encodeURIComponent(objectMatch[2] ?? "")}`,
        signal,
      );
      return response.items;
    }

    // Expression context: suggest columns from tables in FROM/JOIN scope
    const expressionKeywords =
      /\b(?:SELECT|WHERE|AND|OR|ON|HAVING|GROUP\s+BY|ORDER\s+BY|SET|WHEN|THEN|ELSE)\s/i;
    if (expressionKeywords.test(textUntilPosition) && curDb) {
      const tables = extractTablesInScope(curValue);
      if (tables.length > 0) {
        // Fetch columns from all in-scope tables (limit to 5 to avoid too many requests)
        const limitedTables = tables.slice(0, 5);
        const allColumnItems: CompletionResponse["items"] = [];
        const fetches = limitedTables.map(async ({ tableName }) => {
          try {
            const response = await api.get<CompletionResponse>(
              `/query/completions?kind=column&database=${encodeURIComponent(curDb)}&role=${encodeURIComponent(curRole)}&table=${encodeURIComponent(tableName)}`,
              signal,
            );
            return response.items;
          } catch {
            return [];
          }
        });
        const results = await Promise.all(fetches);
        for (const items of results) {
          allColumnItems.push(...items);
        }
        // Deduplicate column labels
        const seenCols = new Set<string>();
        const uniqueCols = allColumnItems.filter((item) => {
          if (seenCols.has(item.label)) return false;
          seenCols.add(item.label);
          return true;
        });
        // Merge with keyword suggestions
        const keywordItems = SQL_KEYWORDS.filter((keyword) =>
          keyword
            .toLowerCase()
            .startsWith(lastWord(textUntilPosition).toLowerCase()),
        ).map((keyword) => ({ label: keyword, type: "keyword" }));
        return [...uniqueCols, ...keywordItems];
      }
    }

    return SQL_KEYWORDS.filter((keyword) =>
      keyword
        .toLowerCase()
        .startsWith(lastWord(textUntilPosition).toLowerCase()),
    ).map((keyword) => ({ label: keyword, type: "keyword" }));
  }

  function handleMount(
    editor: Parameters<
      NonNullable<ComponentProps<typeof Editor>["onMount"]>
    >[0],
    monaco: Monaco,
  ) {
    activeSqlEditorInstance = editor;
    editorRef.current = editor;
    monacoRef.current = monaco;
    setMonacoReady(true);
    // Remember the last non-empty selection. Clicking the Run button blurs the
    // editor and collapses the live selection, which would otherwise make the
    // fallback in `getSqlForExecution` run the whole buffer instead of the
    // highlighted text.
    selectionListenerRef.current?.dispose();
    activeSqlSelection = null;
    selectionListenerRef.current = editor.onDidChangeCursorSelection(
      (event) => {
        activeSqlSelection = event.selection.isEmpty() ? null : event.selection;
      },
    );
    // Define and select the Nova SQL themes (shared with the version preview).
    applyNovaSqlTheme(monaco, resolvedTheme === "dark");

    providerRef.current?.dispose();
    providerRef.current = monaco.languages.registerCompletionItemProvider(
      "sql",
      {
        triggerCharacters: ["@", ".", " "],
        provideCompletionItems: async (
          model: Parameters<
            Monaco["languages"]["registerCompletionItemProvider"]
          >[1] extends {
            provideCompletionItems: infer T;
          }
            ? T extends (...args: infer A) => unknown
              ? A[0]
              : never
            : never,
          position: Parameters<
            Monaco["languages"]["registerCompletionItemProvider"]
          >[1] extends {
            provideCompletionItems: infer T;
          }
            ? T extends (...args: infer A) => unknown
              ? A[1]
              : never
            : never,
        ) => {
          const textUntilPosition = model.getValueInRange({
            startLineNumber: 1,
            startColumn: 1,
            endLineNumber: position.lineNumber,
            endColumn: position.column,
          });
          completionRequestRef.current?.controller.abort();
          const request = {
            id: (completionRequestRef.current?.id ?? 0) + 1,
            controller: new AbortController(),
          };
          completionRequestRef.current = request;

          try {
            const items = await provideCompletions(
              textUntilPosition,
              request.controller.signal,
            );
            if (completionRequestRef.current?.id !== request.id) {
              return { suggestions: [] };
            }

            const stageContext =
              extractStageCompletionContext(textUntilPosition);
            const stageRange = stageContext
              ? getStageCompletionReplacementRange(
                  position.lineNumber,
                  position.column,
                  stageContext,
                )
              : undefined;

            return {
              suggestions: items.map((item) => ({
                label:
                  getStageCompletionKind(item) === "file"
                    ? {
                        label: item.label,
                        description: getStageFileExtension(item) ?? "FILE",
                      }
                    : item.label,
                kind:
                  item.type === "keyword"
                    ? monaco.languages.CompletionItemKind.Keyword
                    : item.type === "database"
                      ? monaco.languages.CompletionItemKind.Module
                      : item.type === "schema"
                        ? monaco.languages.CompletionItemKind.Folder
                        : item.type === "object"
                          ? monaco.languages.CompletionItemKind.Field
                          : item.type === "column"
                            ? monaco.languages.CompletionItemKind.Field
                            : getStageCompletionKind(item) === "stage"
                              ? monaco.languages.CompletionItemKind.Module
                              : getStageCompletionKind(item) === "folder"
                                ? monaco.languages.CompletionItemKind.Folder
                                : getStageCompletionKind(item) === "file"
                                  ? monaco.languages.CompletionItemKind.File
                                  : monaco.languages.CompletionItemKind
                                      .Variable,
                insertText: getStageCompletionInsertText(item),
                sortText:
                  item.type === "stage" ||
                  item.type === "stage_folder" ||
                  item.type === "stage_file"
                    ? getStageCompletionSortText(item)
                    : undefined,
                detail:
                  item.type === "stage" ||
                  item.type === "stage_folder" ||
                  item.type === "stage_file"
                    ? formatStageCompletionDetail(item)
                    : item.detail,
                range:
                  item.type === "stage" ||
                  item.type === "stage_folder" ||
                  item.type === "stage_file"
                    ? stageRange
                    : undefined,
                command: shouldTriggerStageSuggestions(item)
                  ? {
                      id: "editor.action.triggerSuggest",
                      title: "Show stage contents",
                    }
                  : undefined,
              })),
            };
          } catch {
            return { suggestions: [] };
          }
        },
      },
    );

    // SQL Formatting — Format Document + Format Selection
    const formatOpts = {
      language: "mysql" as const,
      keywordCase: "upper" as const,
      tabWidth: 2,
    };
    monaco.languages.registerDocumentFormattingEditProvider("sql", {
      provideDocumentFormattingEdits(model: any) {
        try {
          const formatted = formatSql(model.getValue(), formatOpts);
          return [{ range: model.getFullModelRange(), text: formatted }];
        } catch {
          return [];
        }
      },
    });
    monaco.languages.registerDocumentRangeFormattingEditProvider("sql", {
      provideDocumentRangeFormattingEdits(model: any, range: any) {
        try {
          const text = model.getValueInRange(range);
          const formatted = formatSql(text, formatOpts);
          return [{ range, text: formatted }];
        } catch {
          return [];
        }
      },
    });
    // Ctrl+Shift+F keyboard shortcut for format
    editor.addAction({
      id: "format-sql",
      label: "Format SQL",
      keybindings: [
        monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyF,
      ],
      run: (ed) => {
        ed.getAction("editor.action.formatDocument")?.run();
      },
    });

    // "Attach to Neva" in the editor's right-click menu, in the navigation
    // group so it sits above the native clipboard entries.
    editor.addAction({
      id: "attach-to-nova",
      label: "Attach to Neva",
      contextMenuGroupId: "navigation",
      contextMenuOrder: 1,
      run: () => {
        onAttachSelectionRef.current();
      },
    });

    // Ctrl/Cmd+Enter to run query — DOM listener is more reliable than
    // Monaco's addCommand which can be swallowed by the keybinding service
    const editorDomNode = editor.getDomNode();
    if (editorDomNode) {
      const handler = (e: KeyboardEvent) => {
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
          e.preventDefault();
          e.stopPropagation();
          // Shift runs every statement; plain runs the current one.
          if (e.shiftKey) onRunAllRef.current();
          else onRunRef.current();
        }
      };
      editorDomNode.addEventListener("keydown", handler, true);
    }
  }

  return (
    <div
      className="relative h-full min-h-0"
      onKeyDown={(event) => {
        // Escape dismisses Neva's suggestion, unless the editor used the key
        // itself (closing its own suggestion list).
        if (
          event.key === "Escape" &&
          proposedRewrite &&
          !event.defaultPrevented
        )
          onDenyRewrite();
      }}
    >
      <Editor
        height="100%"
        language="sql"
        theme={resolvedTheme === "dark" ? "nova-dark" : "nova-light"}
        value={value}
        onChange={onChange}
        onMount={handleMount}
        options={{
          readOnly,
          minimap: { enabled: false },
          fontSize: 12,
          fontFamily:
            "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace",
          fontLigatures: true,
          fontWeight: "400",
          lineHeight: 20,
          automaticLayout: true,
          wordWrap: "on",
          wordBasedSuggestions: "off",
          suggest: {
            showWords: false,
          },
          suggestOnTriggerCharacters: true,
          scrollBeyondLastLine: false,
          renderLineHighlight: "none",
          overviewRulerBorder: false,
          hideCursorInOverviewRuler: true,
          padding: { top: 10, bottom: 12 },
        }}
      />
      {proposedRewrite ? (
        <div className="pointer-events-none absolute inset-x-3 bottom-3 z-10 flex flex-col justify-end">
          <RewriteReview
            rewrite={proposedRewrite}
            onCancel={onDenyRewrite}
            onApply={onApproveRewrite}
            onApplyAndRun={onApproveAndRunRewrite}
          />
        </div>
      ) : null}
    </div>
  );
}

function dedupeCompletionItems(items: CompletionResponse["items"]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = `${item.type}:${item.label}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function extractRelationCompletionContext(
  text: string,
):
  | { type: "database"; prefix: string }
  | { type: "schema"; database: string; prefix: string }
  | { type: "object"; database: string; schema: string; prefix: string }
  | null {
  const objectMatch = text.match(
    /\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]*)$/i,
  );
  if (objectMatch) {
    return {
      type: "object",
      database: objectMatch[1],
      schema: objectMatch[2],
      prefix: objectMatch[3] ?? "",
    };
  }

  const schemaMatch = text.match(
    /\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z0-9_]+)\.([A-Za-z0-9_]*)$/i,
  );
  if (schemaMatch) {
    return {
      type: "schema",
      database: schemaMatch[1],
      prefix: schemaMatch[2] ?? "",
    };
  }

  const databaseMatch = text.match(
    /\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z0-9_]*)$/i,
  );
  if (databaseMatch) {
    return {
      type: "database",
      prefix: databaseMatch[1] ?? "",
    };
  }

  return null;
}

function resolveAliasTable(sql: string, alias: string) {
  const regex =
    /\b(?:FROM|JOIN)\s+([A-Za-z0-9_.`]+)(?:\s+(?:AS\s+)?([A-Za-z0-9_]+))?/gi;
  let match: RegExpExecArray | null = regex.exec(sql);
  while (match) {
    if (match[2] === alias) {
      return match[1].replace(/`/g, "").split(".").pop() ?? null;
    }
    match = regex.exec(sql);
  }
  return null;
}

function extractTablesInScope(
  sql: string,
): Array<{ tableName: string; alias: string | null }> {
  const regex =
    /\b(?:FROM|JOIN)\s+([A-Za-z0-9_.`]+)(?:\s+(?:AS\s+)?([A-Za-z0-9_]+))?/gi;
  const tables: Array<{ tableName: string; alias: string | null }> = [];
  const seen = new Set<string>();
  let match: RegExpExecArray | null = regex.exec(sql);
  while (match) {
    const raw = match[1].replace(/`/g, "");
    const tableName = raw.split(".").pop() ?? raw;
    const alias = match[2] ?? null;
    if (!seen.has(tableName)) {
      seen.add(tableName);
      tables.push({ tableName, alias });
    }
    match = regex.exec(sql);
  }
  return tables;
}

function lastWord(text: string) {
  return text.split(/\s+/).pop() ?? "";
}
