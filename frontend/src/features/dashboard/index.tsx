import { useMemo, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  Bot,
  ChevronRight,
  Clock3,
  Database,
  FileCode2,
  FileSearch,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Table2,
  Upload,
  Users,
  WandSparkles,
  Workflow,
} from "lucide-react";
import { api } from "@/lib/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Header } from "@/components/layout/header";
import { Main } from "@/components/layout/main";
import { starterTemplates } from "@/features/workspaces/starter-templates";

type RecentType = "query";
type RecentTab = "all" | RecentType;

type RecentWorkItem = {
  id: string;
  title: string;
  type: RecentType;
  location: string;
  viewed: string;
  updated: string;
  sortTime: number;
  fileId: string | null;
  sql: string | null;
};

type QueryHistoryResponse = {
  items: {
    log_id: string;
    query_id: string;
    event_time: string;
    sql_text: string;
    database_name: string | null;
    schema_name: string | null;
    file_id: string | null;
  }[];
  total: number;
};

type WorkspaceTreeResponse = {
  entries: {
    id: string;
    name: string;
    path: string;
    entry_type: string;
    created_at: string | null;
    updated_at: string | null;
  }[];
};

const RECENT_WORK_LIMIT = 10;

const quickActions = [
  {
    title: "New SQL query",
    description: "Open a worksheet and start querying your data.",
    href: "/workspaces",
    icon: FileCode2,
  },
  {
    title: "Upload files to stage",
    description: "Load local data through a governed stage.",
    href: "/workspaces",
    icon: Upload,
  },
  {
    title: "Browse databases",
    description: "Explore schemas, tables, views, and columns.",
    href: "/database-explorer",
    icon: Database,
  },
  {
    title: "Manage users",
    description: "Review identities, roles, and privileges.",
    href: "/users",
    icon: Users,
  },
];

const starterTemplateIcons = {
  "query-stage": FileSearch,
  "demo-sales": Table2,
  "profile-sales": Clock3,
  "demo-order-anomalies": WandSparkles,
  "classify-products": Bot,
  "daily-sales-task": Workflow,
};

const recentTabs: { value: RecentTab; label: string }[] = [
  { value: "all", label: "All" },
  { value: "query", label: "Queries" },
];

const recentTypeMeta: Record<
  RecentType,
  { label: string; icon: typeof FileCode2 }
> = {
  query: { label: "SQL query", icon: FileCode2 },
};

export function Dashboard() {
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState("");
  const normalizedQuery = searchQuery.trim().toLowerCase();

  const workspaceQuery = useQuery<WorkspaceTreeResponse>({
    queryKey: ["dashboard", "workspace-tree"],
    queryFn: () => api.get<WorkspaceTreeResponse>("/workspaces/tree"),
  });

  const queryHistoryQuery = useQuery<QueryHistoryResponse>({
    queryKey: ["dashboard", "query-history"],
    queryFn: () => api.get<QueryHistoryResponse>("/query/history?limit=10"),
  });

  const recentWork = useMemo(
    () =>
      buildRecentWorkItems({
        workspace: workspaceQuery.data,
        queryHistory: queryHistoryQuery.data,
      }),
    [queryHistoryQuery.data, workspaceQuery.data],
  );

  const recentWorkLoading =
    workspaceQuery.isLoading || queryHistoryQuery.isLoading;

  const recentWorkHasError =
    workspaceQuery.isError || queryHistoryQuery.isError;

  const openRecentWork = (item: RecentWorkItem) => {
    void navigate({
      to: "/workspaces",
      search: {
        file: item.fileId ?? undefined,
        q: item.sql ?? undefined,
      },
    });
  };

  const filteredRecentWork = useMemo(
    () =>
      recentWork.filter((item) => {
        if (!normalizedQuery) return true;
        return [item.title, item.type, item.location]
          .join(" ")
          .toLowerCase()
          .includes(normalizedQuery);
      }),
    [normalizedQuery, recentWork],
  );

  const filteredTemplates = useMemo(
    () =>
      starterTemplates.filter((item) => {
        if (!normalizedQuery) return true;
        return [item.title, item.category, item.topic]
          .join(" ")
          .toLowerCase()
          .includes(normalizedQuery);
      }),
    [normalizedQuery],
  );

  return (
    <>
      <Header>
        <div className="me-auto" />
        <Button asChild>
          <Link to="/workspaces">
            <Plus className="size-4" />
            New query
          </Link>
        </Button>
      </Header>

      <Main fixed fluid className="min-h-0 p-0">
        <div className="minimal-scrollbar min-h-0 w-full flex-1 overflow-y-auto">
          <div className="space-y-10 px-4 py-6 pb-12 @7xl/content:mx-auto @7xl/content:w-full @7xl/content:max-w-7xl">
            <section className="space-y-6">
              <div className="flex flex-col gap-3">
                <h1 className="text-2xl leading-8 font-normal">Home</h1>
              </div>

              <div className="relative max-w-5xl">
                <Search className="pointer-events-none absolute start-4 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  className="h-11 rounded-md border-border/80 bg-background ps-11 text-sm shadow-none"
                  placeholder="Search Nova objects and docs"
                />
              </div>
            </section>

            <section className="space-y-4">
              <h2 className="text-lg font-heading">Quick actions</h2>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {quickActions.map((action) => (
                  <Link
                    key={action.title}
                    to={action.href}
                    className="group flex min-h-28 rounded-lg border border-border/75 bg-card p-4 transition-colors hover:border-primary/35 hover:bg-primary/3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <div className="flex min-w-0 flex-col gap-3">
                      <action.icon className="size-4 text-primary" />
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium">
                            {action.title}
                          </span>
                          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                        </div>
                        <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">
                          {action.description}
                        </p>
                      </div>
                    </div>
                  </Link>
                ))}
              </div>
            </section>

            <section className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-heading">Recent work</h2>
                <Button variant="ghost" size="sm" asChild>
                  <Link to="/workspaces">View all</Link>
                </Button>
              </div>

              <Tabs defaultValue="all" className="gap-4">
                <div className="w-full overflow-x-auto border-b border-border/80">
                  <TabsList indicatorVariant="underline" className="h-10 rounded-none bg-transparent p-0">
                    {recentTabs.map((tab) => (
                      <TabsTrigger
                        key={tab.value}
                        value={tab.value}
                        className="rounded-none border-0 bg-transparent px-0 me-7 text-muted-foreground shadow-none data-[state=active]:bg-transparent data-[state=active]:text-primary data-[state=active]:shadow-none"
                      >
                        {tab.label}
                      </TabsTrigger>
                    ))}
                  </TabsList>
                </div>

                {recentTabs.map((tab) => (
                  <TabsContent key={tab.value} value={tab.value}>
                    <RecentWorkTable
                      items={filteredRecentWork.filter(
                        (item) =>
                          tab.value === "all" || item.type === tab.value,
                      )}
                      hasError={recentWorkHasError}
                      loading={recentWorkLoading}
                      onSelect={openRecentWork}
                    />
                  </TabsContent>
                ))}
              </Tabs>
            </section>

            <section id="starter-templates" className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div className="space-y-1">
                  <h2 className="text-lg font-heading">
                    Start with a template
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    Explore Nova features with editable SQL and sample data.
                  </p>
                </div>
                <Button variant="ghost" size="sm" asChild>
                  <a href="#starter-templates">Browse templates</a>
                </Button>
              </div>

              <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
                {filteredTemplates.map((template) => {
                  const Icon = starterTemplateIcons[template.id];
                  return (
                    <Link
                      key={template.id}
                      to="/workspaces"
                      search={{ template: template.id }}
                      className="group flex min-h-20 items-center justify-between gap-4 rounded-lg border border-border/75 bg-card px-4 py-3 transition-colors hover:border-primary/35 hover:bg-primary/3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <div className="flex min-w-0 items-start gap-3">
                        <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground group-hover:text-primary" />
                        <div className="min-w-0 space-y-2">
                          <p className="truncate text-sm font-medium">
                            {template.title}
                          </p>
                          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            <span>{template.category}</span>
                            <span aria-hidden="true">-</span>
                            <span>{template.topic}</span>
                          </div>
                        </div>
                      </div>
                      <Plus className="size-4 shrink-0 text-muted-foreground group-hover:text-primary" />
                    </Link>
                  );
                })}
              </div>
            </section>
          </div>
        </div>
      </Main>
    </>
  );
}

function buildRecentWorkItems({
  workspace,
  queryHistory,
}: {
  workspace?: WorkspaceTreeResponse;
  queryHistory?: QueryHistoryResponse;
}): RecentWorkItem[] {
  const workspaceItems =
    workspace?.entries
      .filter((entry) => entry.entry_type === "file")
      .map((entry) => {
        const updatedAt = entry.updated_at ?? entry.created_at;
        return {
          id: `workspace:${entry.id}`,
          title: entry.name,
          type: "query" as const,
          location: entry.path || "WORKSHEETS",
          viewed: formatRelativeTime(updatedAt),
          updated: formatRelativeTime(updatedAt),
          sortTime: toTime(updatedAt),
          fileId: entry.id,
          sql: null,
        };
      }) ?? [];

  const queryItems =
    queryHistory?.items.map((item) => {
      const title = compactSql(item.sql_text);
      const location = [item.database_name, item.schema_name]
        .filter(Boolean)
        .join(".");
      return {
        id: `query:${item.log_id || item.query_id}`,
        title,
        type: "query" as const,
        location: location || "QUERY HISTORY",
        viewed: formatRelativeTime(item.event_time),
        updated: formatRelativeTime(item.event_time),
        sortTime: toTime(item.event_time),
        fileId: item.file_id,
        sql: item.sql_text,
      };
    }) ?? [];

  return [...workspaceItems, ...queryItems]
    .sort((a, b) => b.sortTime - a.sortTime || a.title.localeCompare(b.title))
    .slice(0, RECENT_WORK_LIMIT);
}

function toTime(value: string | null | undefined) {
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function formatRelativeTime(value: string | null | undefined) {
  const time = toTime(value);
  if (!time) return "-";

  const diffMs = Date.now() - time;
  if (diffMs < 0) return "Just now";

  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;

  if (diffMs < minute) return "Just now";
  if (diffMs < hour) return `${Math.floor(diffMs / minute)}m ago`;
  if (diffMs < day) return `${Math.floor(diffMs / hour)}h ago`;
  if (diffMs < 7 * day) return `${Math.floor(diffMs / day)}d ago`;

  return new Date(time).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year:
      new Date(time).getFullYear() === new Date().getFullYear()
        ? undefined
        : "numeric",
  });
}

function compactSql(sql: string) {
  const compact = sql.replace(/\s+/g, " ").trim();
  if (!compact) return "Untitled query";
  return compact.length > 72 ? `${compact.slice(0, 72).trim()}...` : compact;
}

function RecentWorkTable({
  items,
  loading,
  hasError,
  onSelect,
}: {
  items: RecentWorkItem[];
  loading: boolean;
  hasError: boolean;
  onSelect: (item: RecentWorkItem) => void;
}) {
  if (loading && items.length === 0) {
    return (
      <div className="space-y-3 py-2">
        {Array.from({ length: 5 }).map((_, index) => (
          <div
            key={index}
            className="h-11 animate-pulse rounded-md bg-muted/70"
          />
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex min-h-44 flex-col items-center justify-center rounded-lg border border-dashed border-border/80 text-center">
        <Sparkles className="mb-3 size-5 text-muted-foreground" />
        <p className="text-sm font-medium">No matching work found</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Try another search term or switch tabs.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3 overflow-x-auto">
      {hasError && (
        <div className="flex items-center gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning-strong">
          <AlertCircle className="size-3.5 shrink-0" />
          Some recent work sources could not be loaded.
        </div>
      )}
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="min-w-[260px]">Title</TableHead>
            <TableHead className="min-w-[150px]">Type</TableHead>
            <TableHead className="min-w-[220px]">Location</TableHead>
            <TableHead className="min-w-[130px]">Viewed</TableHead>
            <TableHead className="min-w-[130px] text-right">Updated</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => {
            const meta = recentTypeMeta[item.type];
            return (
              <TableRow
                key={item.id}
                role="button"
                tabIndex={0}
                className="cursor-pointer focus-visible:bg-muted/60 focus-visible:outline-none"
                onClick={() => onSelect(item)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(item);
                  }
                }}
              >
                <TableCell>
                  <div className="flex min-w-0 items-center gap-3">
                    <meta.icon className="size-4 shrink-0 text-muted-foreground" />
                    <span className="truncate font-medium">{item.title}</span>
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant="secondary" className="font-normal">
                    {meta.label}
                  </Badge>
                </TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">
                  {item.location}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {item.viewed}
                </TableCell>
                <TableCell className="text-right text-muted-foreground">
                  {item.updated}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <div className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
        <ShieldCheck className="size-3.5" />
        Stage access follows schema privileges and never exposes credentials.
      </div>
    </div>
  );
}
