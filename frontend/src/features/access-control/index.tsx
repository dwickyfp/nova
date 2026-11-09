import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronsUpDown, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { z } from "zod";
import { useNevaSurface } from "@/features/assistant/neva-surface-hook";
import { defineNevaCapability } from "@/features/assistant/surface-registry";
import {
  SimpleTablePagination,
  SimpleTableToolbar,
  SimpleTableViewport,
} from "@/components/data-table/simple-table-controls";
import { Header } from "@/components/layout/header";
import { Main } from "@/components/layout/main";
import { Search } from "@/components/search";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { SearchableSelect } from "@/components/ui/searchable-select";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StateBadge, StatusBadge } from "@/components/ui/status-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api-client";
import { cn } from "@/lib/utils";

type Health = {
  healthy: boolean;
  service_name?: string;
  pending_propagation?: number;
  drift_count?: number;
  reason?: string;
};

type Policy = {
  id?: number;
  name: string;
  policyType?: number;
  resources?: Record<string, { values?: string[] }>;
  policyItems?: Array<{ roles?: string[]; accesses?: Array<{ type: string }> }>;
  rowFilterPolicyItems?: Array<{ roles?: string[] }>;
  dataMaskPolicyItems?: Array<{ roles?: string[] }>;
};

type Role = {
  id?: number;
  name: string;
  description?: string;
  users?: Array<{ name: string }>;
};

type AdminUser = { username: string; host?: string };
type NamedItem = string | { name: string };

type EffectiveAccess = {
  principal: string;
  active_role: string;
  resource: string;
  authorization_provider: string;
  policy_health: string;
  policies: Policy[];
  object_access: string[];
  row_restrictions: string[];
  column_restrictions: Record<string, string>;
};

const PERMISSION_OPTIONS = ["SELECT", "INSERT", "UPDATE", "DELETE"];
/** What each engine permission lets a person do, in words. */
const PERMISSION_LABELS: Record<string, string> = {
  SELECT: "Read",
  INSERT: "Add rows",
  UPDATE: "Change rows",
  DELETE: "Delete rows",
};
const MASK_OPTIONS = [
  { value: "MASK", label: "Show as xxxx" },
  { value: "MASK_HASH", label: "Replace with a hash" },
  { value: "MASK_NULL", label: "Show as empty" },
];

/** A policy's kind in the words the Data access tab uses to create it. */
function policyKind(policy: Policy) {
  if (policy.policyType === 1) return "Hidden column";
  if (policy.policyType === 2) return "Row limit";
  return "Table access";
}

function policyRoles(policy: Policy) {
  return [
    ...(policy.policyItems ?? []),
    ...(policy.rowFilterPolicyItems ?? []),
    ...(policy.dataMaskPolicyItems ?? []),
  ]
    .flatMap((item) => item.roles ?? [])
    .join(", ");
}

function policyResource(policy: Policy) {
  return Object.entries(policy.resources ?? {})
    .map(([key, value]) => `${key}: ${(value.values ?? []).join(", ")}`)
    .join(" / ");
}

function itemNames(items: NamedItem[] | undefined) {
  return (items ?? []).map((item) =>
    typeof item === "string" ? item : item.name,
  );
}

function pageSlice<T>(items: T[], page: number, pageSize: number) {
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const currentPage = Math.min(Math.max(page, 1), totalPages);
  const start = (currentPage - 1) * pageSize;
  return { currentPage, items: items.slice(start, start + pageSize) };
}

function useTables(database: string) {
  return useQuery({
    queryKey: ["access-control", "tables", database],
    queryFn: () =>
      api.get<{ tables: NamedItem[] }>(
        `/objects/databases/${encodeURIComponent(database)}/tables`,
      ),
    enabled: Boolean(database),
    select: (result) => itemNames(result.tables),
  });
}

function useColumns(database: string, table: string) {
  return useQuery({
    queryKey: ["access-control", "columns", database, table],
    queryFn: () =>
      api.get<{ columns: NamedItem[] }>(
        `/objects/databases/${encodeURIComponent(database)}/tables/${encodeURIComponent(table)}/columns`,
      ),
    enabled: Boolean(database && table),
    select: (result) => itemNames(result.columns),
  });
}

function TextField({
  id,
  label,
  value,
  placeholder,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function SelectField({
  label,
  value,
  options,
  placeholder,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  placeholder: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <SearchableSelect
        options={options}
        value={value}
        onChange={onChange}
        label={label}
        emptyLabel={placeholder}
        placeholder={`Search ${label.toLowerCase()}...`}
        allowEmpty={false}
        disabled={disabled}
        className="h-9 w-full justify-between"
      />
    </div>
  );
}

function MultiSelectField({
  label,
  values,
  options,
  labels = {},
  placeholder,
  onChange,
}: {
  label: string;
  values: string[];
  options: string[];
  /** Display text per option value; the value is shown when absent. */
  labels?: Record<string, string>;
  placeholder: string;
  onChange: (values: string[]) => void;
}) {
  const shown = (option: string) => labels[option] ?? option;
  const [open, setOpen] = useState(false);

  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            role="combobox"
            aria-label={label}
            aria-expanded={open}
            className="h-9 w-full justify-between px-3 font-normal"
          >
            <span
              className={cn(
                "truncate",
                !values.length && "text-muted-foreground",
              )}
            >
              {values.length ? values.map(shown).join(", ") : placeholder}
            </span>
            <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className="w-[var(--radix-popover-trigger-width)] p-0"
        >
          <Command>
            <CommandInput placeholder={`Search ${label.toLowerCase()}...`} />
            <CommandList>
              <CommandEmpty>No options found.</CommandEmpty>
              <CommandGroup>
                {options.map((option) => {
                  const selected = values.includes(option);
                  return (
                    <CommandItem
                      key={option}
                      value={shown(option)}
                      onSelect={() =>
                        onChange(
                          selected
                            ? values.filter((value) => value !== option)
                            : [...values, option],
                        )
                      }
                    >
                      <Check
                        className={cn(
                          "size-4",
                          selected ? "opacity-100" : "opacity-0",
                        )}
                      />
                      {shown(option)}
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  );
}

function ChoiceField({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id} className="h-9 w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function Section({
  title,
  description,
  step,
  children,
}: {
  title: string;
  description: string;
  /** Position in a sequence of tasks, shown before the title. */
  step?: number;
  children: React.ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className="rounded-lg border bg-background px-5 py-5 sm:px-6"
    >
      <div className="flex gap-3">
        {step ? (
          <span
            aria-hidden="true"
            className="grid size-6 shrink-0 place-items-center rounded-full border text-xs tabular-nums text-muted-foreground"
          >
            {step}
          </span>
        ) : null}
        <div className="min-w-0">
          <h2 className="text-base font-medium">{title}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        </div>
      </div>
      <div className="mt-5">{children}</div>
    </section>
  );
}

/** One plain sentence that says what a tab is for, above its content. */
function TabLead({ children }: { children: React.ReactNode }) {
  return <p className="max-w-3xl text-sm text-muted-foreground">{children}</p>;
}

/**
 * Ranger's state as one quiet line next to the page actions: it belongs to the
 * whole page, so it stays put whichever tab is open.
 */
function RangerStatus({ state, loading }: { state?: Health; loading: boolean }) {
  if (loading && !state) {
    return <span className="text-sm text-muted-foreground">Checking Ranger…</span>;
  }
  const pending = state?.pending_propagation ?? 0;
  const drift = state?.drift_count ?? 0;
  return (
    <div
      aria-label="Authorization status"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
    >
      <StatusBadge tone={state?.healthy ? "success" : "warning"} dot>
        {state?.healthy ? "Ranger healthy" : `Ranger ${state?.reason || "unavailable"}`}
      </StatusBadge>
      {pending ? (
        <span
          className="text-muted-foreground tabular-nums"
          title="Changes Ranger has accepted that are still reaching the engine"
        >
          {pending} {pending === 1 ? "change" : "changes"} syncing
        </span>
      ) : null}
      {drift ? (
        <StatusBadge tone="warning">
          {drift} out of sync
        </StatusBadge>
      ) : null}
    </div>
  );
}

function PolicyTable({ policies }: { policies: Policy[] }) {
  return (
    <SimpleTableViewport className="max-h-none">
      <Table className="min-w-[760px]">
        <TableHeader>
          <TableRow>
            <TableHead className="w-[30%] px-4">Policy</TableHead>
            <TableHead className="w-36">Kind</TableHead>
            <TableHead>Applies to</TableHead>
            <TableHead className="w-[22%] px-4">Roles</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {policies.map((policy) => (
            <TableRow key={policy.id ?? policy.name}>
              <TableCell className="px-4 font-medium">{policy.name}</TableCell>
              <TableCell>
                <StatusBadge>{policyKind(policy)}</StatusBadge>
              </TableCell>
              <TableCell className="max-w-md whitespace-normal text-muted-foreground">
                {policyResource(policy) || "Everything Nova manages"}
              </TableCell>
              <TableCell className="px-4 whitespace-normal">
                {policyRoles(policy) || "No role"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </SimpleTableViewport>
  );
}

export function AccessControlPage() {
  const queryClient = useQueryClient();
  const [newRole, setNewRole] = useState("");
  const [newRoleDescription, setNewRoleDescription] = useState("");
  const [roleSearch, setRoleSearch] = useState("");
  const [rolePage, setRolePage] = useState(1);
  const [rolePageSize, setRolePageSize] = useState(10);
  const [policySearch, setPolicySearch] = useState("");
  const [policyPage, setPolicyPage] = useState(1);
  const [policyPageSize, setPolicyPageSize] = useState(10);

  const [accessRole, setAccessRole] = useState("");
  const [accessDatabase, setAccessDatabase] = useState("");
  const [accessTable, setAccessTable] = useState("");
  const [accesses, setAccesses] = useState<string[]>(["SELECT"]);

  const [scopePrincipal, setScopePrincipal] = useState("");
  const [scopeRole, setScopeRole] = useState("");
  const [scopeDatabase, setScopeDatabase] = useState("");
  const [scopeTable, setScopeTable] = useState("");
  const [scopeDimension, setScopeDimension] = useState("");
  const [scopeColumn, setScopeColumn] = useState("");
  const [scopeValues, setScopeValues] = useState("");

  const [maskRole, setMaskRole] = useState("");
  const [maskDatabase, setMaskDatabase] = useState("");
  const [maskTable, setMaskTable] = useState("");
  const [maskColumn, setMaskColumn] = useState("");
  const [maskType, setMaskType] = useState("MASK");

  const [effectivePrincipal, setEffectivePrincipal] = useState("");
  const [effectiveRole, setEffectiveRole] = useState("");
  const [effectiveDatabase, setEffectiveDatabase] = useState("");
  const [effectiveTable, setEffectiveTable] = useState("");
  const [effectivePolicyPage, setEffectivePolicyPage] = useState(1);
  const [effectivePolicyPageSize, setEffectivePolicyPageSize] = useState(10);

  const health = useQuery({
    queryKey: ["access-control", "health"],
    queryFn: () => api.get<Health>("/access-control/health"),
  });
  const policies = useQuery({
    queryKey: ["access-control", "policies"],
    queryFn: () => api.get<{ policies: Policy[] }>("/access-control/policies"),
  });
  const roles = useQuery({
    queryKey: ["access-control", "roles"],
    queryFn: () => api.get<{ roles: Role[] }>("/access-control/roles"),
  });
  const users = useQuery({
    queryKey: ["access-control", "users"],
    queryFn: () => api.get<{ users: AdminUser[] }>("/users"),
  });
  const databases = useQuery({
    queryKey: ["access-control", "databases"],
    queryFn: () => api.get<{ databases: string[] }>("/users/databases"),
  });

  const accessTables = useTables(accessDatabase);
  const scopeTables = useTables(scopeDatabase);
  const scopeColumns = useColumns(scopeDatabase, scopeTable);
  const maskTables = useTables(maskDatabase);
  const maskColumns = useColumns(maskDatabase, maskTable);
  const effectiveTables = useTables(effectiveDatabase);

  const refreshManagedState = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["access-control", "roles"] }),
      queryClient.invalidateQueries({
        queryKey: ["access-control", "policies"],
      }),
      queryClient.invalidateQueries({ queryKey: ["access-control", "health"] }),
    ]);

  const createRole = useMutation({
    mutationFn: () =>
      api.post("/access-control/roles", {
        name: newRole.trim(),
        description: newRoleDescription.trim(),
      }),
    onSuccess: async () => {
      setNewRole("");
      setNewRoleDescription("");
      await refreshManagedState();
      toast.success("Role created");
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Role creation failed",
      ),
  });

  const grantAccess = useMutation({
    mutationFn: () =>
      api.post("/access-control/data-access", {
        role: accessRole,
        catalog: "default_catalog",
        database: accessDatabase,
        table: accessTable,
        accesses,
      }),
    onSuccess: async () => {
      await refreshManagedState();
      toast.success("Permission sent to Ranger");
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Permission update failed",
      ),
  });

  const putScope = useMutation({
    mutationFn: () =>
      api.post("/access-control/data-scopes", {
        principal: scopePrincipal,
        role: scopeRole,
        catalog: "default_catalog",
        database: scopeDatabase,
        table: scopeTable,
        bindings: [
          {
            dimension: scopeDimension,
            column: scopeColumn,
            values: scopeValues
              .split(",")
              .map((value) => value.trim())
              .filter(Boolean),
          },
        ],
      }),
    onSuccess: async () => {
      await refreshManagedState();
      toast.success("Data scope sent to Ranger");
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Data scope update failed",
      ),
  });

  const putMask = useMutation({
    mutationFn: () =>
      api.post("/access-control/masks", {
        role: maskRole,
        catalog: "default_catalog",
        database: maskDatabase,
        table: maskTable,
        column: maskColumn,
        mask_type: maskType,
      }),
    onSuccess: async () => {
      await refreshManagedState();
      toast.success("Column mask sent to Ranger");
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Mask update failed",
      ),
  });

  const effective = useMutation({
    mutationFn: () =>
      api.post<EffectiveAccess>("/access-control/effective-access", {
        principal: effectivePrincipal,
        active_role: effectiveRole,
        resource: `${effectiveDatabase}.${effectiveTable}`,
      }),
    onSuccess: () => setEffectivePolicyPage(1),
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Inspection failed"),
  });

  const state = health.data;
  const managedPolicies = policies.data?.policies ?? [];
  const managedRoles = roles.data?.roles ?? [];
  const roleOptions = managedRoles.map((item) => item.name);
  const userOptions = Array.from(
    new Set((users.data?.users ?? []).map((user) => user.username)),
  );
  const databaseOptions = databases.data?.databases ?? [];

  const filteredRoles = useMemo(() => {
    const query = roleSearch.trim().toLowerCase();
    return managedRoles.filter((item) =>
      [
        item.name,
        item.description ?? "",
        ...(item.users ?? []).map((user) => user.name),
      ]
        .join(" ")
        .toLowerCase()
        .includes(query),
    );
  }, [managedRoles, roleSearch]);
  const visibleRoles = pageSlice(filteredRoles, rolePage, rolePageSize);

  const filteredPolicies = useMemo(() => {
    const query = policySearch.trim().toLowerCase();
    return managedPolicies.filter((policy) =>
      [
        policy.name,
        policyKind(policy),
        policyRoles(policy),
        policyResource(policy),
      ]
        .join(" ")
        .toLowerCase()
        .includes(query),
    );
  }, [managedPolicies, policySearch]);
  useNevaSurface({
    id: "access_control",
    route: "/access-control",
    title: "Data Access",
    context: () => ({
      view: {
        filters: { roleSearch, policySearch },
        summary: {
          roles: filteredRoles.length,
          policies: filteredPolicies.length,
          healthy: health.data?.healthy ?? null,
          changesWaitingToApply: health.data?.pending_propagation ?? null,
          drift: health.data?.drift_count ?? null,
        },
      },
    }),
    capabilities: [
      defineNevaCapability({
        name: "surface.refresh", risk: "safe", argsSchema: z.object({}),
        execute: () => refreshManagedState(),
      }),
      defineNevaCapability({
        name: "surface.set_filter", risk: "safe",
        argsSchema: z.object({ filter: z.enum(["roles", "policies"]), value: z.string().max(120) }),
        execute: ({ filter, value }) => {
          if (filter === "roles") {
            setRoleSearch(value);
            setRolePage(1);
          } else {
            setPolicySearch(value);
            setPolicyPage(1);
          }
        },
      }),
    ],
    suggestedActions: [
      { label: "Who can read a table", prompt: "Which roles can read the sales_orders table?" },
      { label: "Hide a column", prompt: "How do I hide a column from a role?" },
    ],
  });
  const visiblePolicies = pageSlice(
    filteredPolicies,
    policyPage,
    policyPageSize,
  );

  const effectivePolicies = effective.data?.policies ?? [];
  const visibleEffectivePolicies = pageSlice(
    effectivePolicies,
    effectivePolicyPage,
    effectivePolicyPageSize,
  );

  return (
    <>
      <Header fixed>
        <Search />
      </Header>
      <Main scroll className="flex flex-1 flex-col gap-6 pb-12">
        <PageHeader
          title="Access Control"
          description="Decide who can see which data. Ranger enforces every rule you set here."
          actions={
            <>
              <RangerStatus state={state} loading={health.isPending} />
              <Button
                variant="outline"
                size="sm"
                disabled={
                  health.isFetching || policies.isFetching || roles.isFetching
                }
                onClick={() =>
                  void Promise.all([
                    health.refetch(),
                    policies.refetch(),
                    roles.refetch(),
                    users.refetch(),
                    databases.refetch(),
                  ])
                }
              >
                <RefreshCw
                  className={cn("size-4", health.isFetching && "animate-spin")}
                />
                Refresh
              </Button>
            </>
          }
        />

        <Tabs defaultValue="roles" className="gap-0">
          <TabsList className="h-auto max-w-full justify-start self-start overflow-x-auto [scrollbar-width:none] [&>[role=tab]]:flex-none">
            {[
              ["roles", "Roles"],
              ["data-access", "Data access"],
              ["policies", "Policies"],
              ["effective", "Check access"],
            ].map(([value, label]) => (
              <TabsTrigger key={value} value={value}>
                {label}
              </TabsTrigger>
            ))}
          </TabsList>

          <TabsContent value="roles" className="space-y-6 pt-5">
            <TabLead>
              A role is a group of people who need the same data, such as
              Sales Jakarta. People work under one role at a time and see
              exactly what that role is allowed.
            </TabLead>
            <Section
              title="Roles"
              description="Every role, and how many people have it."
            >
              <div className="space-y-4">
                <SimpleTableToolbar
                  search={roleSearch}
                  onSearchChange={(value) => {
                    setRoleSearch(value);
                    setRolePage(1);
                  }}
                  searchPlaceholder="Search roles or people"
                  resultLabel={`${filteredRoles.length} ${filteredRoles.length === 1 ? "role" : "roles"}`}
                />
                {filteredRoles.length ? (
                  <>
                    <SimpleTableViewport className="max-h-none">
                      <Table className="min-w-[640px]">
                        <TableHeader>
                          <TableRow>
                            <TableHead className="w-[30%] px-4">Role</TableHead>
                            <TableHead>Description</TableHead>
                            <TableHead className="w-36 px-4 text-right">
                              People
                            </TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {visibleRoles.items.map((item) => (
                            <TableRow key={item.id ?? item.name}>
                              <TableCell className="px-4 font-medium">
                                {item.name}
                              </TableCell>
                              <TableCell className="whitespace-normal text-muted-foreground">
                                {item.description || "No description"}
                              </TableCell>
                              <TableCell className="px-4 text-right tabular-nums">
                                {item.users?.length ?? 0}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </SimpleTableViewport>
                    <SimpleTablePagination
                      page={visibleRoles.currentPage}
                      pageSize={rolePageSize}
                      total={filteredRoles.length}
                      onPageChange={setRolePage}
                      onPageSizeChange={(value) => {
                        setRolePageSize(value);
                        setRolePage(1);
                      }}
                    />
                  </>
                ) : (
                  <EmptyState
                    title={
                      roleSearch
                        ? "No matching roles"
                        : "No authorization roles"
                    }
                    description={
                      roleSearch
                        ? "Try a different role or person."
                        : "Create the first role below, then give it data on the Data access tab."
                    }
                  />
                )}
              </div>
            </Section>

            <Section
              title="Create a role"
              description="Name the group. Give it data afterwards on the Data access tab."
            >
              <div className="grid gap-4 lg:grid-cols-2">
                <TextField
                  id="role-name"
                  label="Name"
                  placeholder="SALES_JAKARTA"
                  value={newRole}
                  onChange={setNewRole}
                />
                <TextField
                  id="role-description"
                  label="Description"
                  placeholder="Sales team in Jakarta"
                  value={newRoleDescription}
                  onChange={setNewRoleDescription}
                />
              </div>
              <div className="mt-5 flex justify-end">
                <Button
                  disabled={!newRole.trim() || createRole.isPending}
                  onClick={() => createRole.mutate()}
                >
                  Create role
                </Button>
              </div>
            </Section>
          </TabsContent>

          <TabsContent value="policies" className="space-y-6 pt-5">
            <TabLead>
              Policies are the rules Ranger enforces. Nova writes them for you
              from the Data access tab, so this list is for review.
            </TabLead>
            <Section
              title="Policies"
              description="Table access, row limits and hidden columns, with the roles they apply to."
            >
              <div className="space-y-4">
                <SimpleTableToolbar
                  search={policySearch}
                  onSearchChange={(value) => {
                    setPolicySearch(value);
                    setPolicyPage(1);
                  }}
                  searchPlaceholder="Search policies, roles or tables"
                  resultLabel={`${filteredPolicies.length} ${filteredPolicies.length === 1 ? "policy" : "policies"}`}
                />
                {filteredPolicies.length ? (
                  <>
                    <PolicyTable policies={visiblePolicies.items} />
                    <SimpleTablePagination
                      page={visiblePolicies.currentPage}
                      pageSize={policyPageSize}
                      total={filteredPolicies.length}
                      onPageChange={setPolicyPage}
                      onPageSizeChange={(value) => {
                        setPolicyPageSize(value);
                        setPolicyPage(1);
                      }}
                    />
                  </>
                ) : (
                  <EmptyState
                    title={
                      policySearch
                        ? "No matching policies"
                        : "No managed policies"
                    }
                    description={
                      policySearch
                        ? "Try a different policy, role, or resource name."
                        : "Give a role data on the Data access tab and its policies appear here."
                    }
                  />
                )}
              </div>
            </Section>
          </TabsContent>

          <TabsContent value="data-access" className="space-y-6 pt-5">
            <TabLead>
              Give a role data in up to three steps: let it use a table, then
              narrow what one person sees, and hide columns that are
              sensitive.
            </TabLead>
            <Section
              step={1}
              title="Let a role use a table"
              description="Without this, the role cannot see the table at all."
            >
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <SelectField
                  label="Role"
                  value={accessRole}
                  options={roleOptions}
                  placeholder="Select role"
                  onChange={setAccessRole}
                />
                <SelectField
                  label="Database"
                  value={accessDatabase}
                  options={databaseOptions}
                  placeholder="Select database"
                  onChange={(value) => {
                    setAccessDatabase(value);
                    setAccessTable("");
                  }}
                />
                <SelectField
                  label="Table"
                  value={accessTable}
                  options={accessTables.data ?? []}
                  placeholder={
                    accessDatabase ? "Select table" : "Select database first"
                  }
                  disabled={!accessDatabase || accessTables.isLoading}
                  onChange={setAccessTable}
                />
                <MultiSelectField
                  label="Allowed to"
                  values={accesses}
                  options={PERMISSION_OPTIONS}
                  labels={PERMISSION_LABELS}
                  placeholder="Choose what it may do"
                  onChange={setAccesses}
                />
              </div>
              <div className="mt-5 flex justify-end">
                <Button
                  disabled={
                    !accessRole ||
                    !accessDatabase ||
                    !accessTable ||
                    !accesses.length ||
                    grantAccess.isPending
                  }
                  onClick={() => grantAccess.mutate()}
                >
                  Give access
                </Button>
              </div>
            </Section>

            <Section
              step={2}
              title="Show a person only some rows"
              description="Under this role, the person sees only the rows that match their values, such as their own cities."
            >
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <SelectField
                  label="Person"
                  value={scopePrincipal}
                  options={userOptions}
                  placeholder="Select user"
                  onChange={setScopePrincipal}
                />
                <SelectField
                  label="Role"
                  value={scopeRole}
                  options={roleOptions}
                  placeholder="Select role"
                  onChange={setScopeRole}
                />
                <SelectField
                  label="Database"
                  value={scopeDatabase}
                  options={databaseOptions}
                  placeholder="Select database"
                  onChange={(value) => {
                    setScopeDatabase(value);
                    setScopeTable("");
                    setScopeDimension("");
                    setScopeColumn("");
                  }}
                />
                <SelectField
                  label="Table"
                  value={scopeTable}
                  options={scopeTables.data ?? []}
                  placeholder={
                    scopeDatabase ? "Select table" : "Select database first"
                  }
                  disabled={!scopeDatabase || scopeTables.isLoading}
                  onChange={(value) => {
                    setScopeTable(value);
                    setScopeDimension("");
                    setScopeColumn("");
                  }}
                />
                <SelectField
                  label="Limit by"
                  value={scopeDimension}
                  options={scopeColumns.data ?? []}
                  placeholder={
                    scopeTable ? "Select what to limit by" : "Select table first"
                  }
                  disabled={!scopeTable || scopeColumns.isLoading}
                  onChange={(value) => {
                    setScopeDimension(value);
                    if (!scopeColumn) setScopeColumn(value);
                  }}
                />
                <SelectField
                  label="Matching column"
                  value={scopeColumn}
                  options={scopeColumns.data ?? []}
                  placeholder={
                    scopeTable ? "Select column" : "Select table first"
                  }
                  disabled={!scopeTable || scopeColumns.isLoading}
                  onChange={setScopeColumn}
                />
                <div className="md:col-span-2">
                  <TextField
                    id="scope-values"
                    label="Allowed values"
                    value={scopeValues}
                    placeholder="Jakarta, Bandung"
                    onChange={setScopeValues}
                  />
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    Separate values with commas. A row matching any of them is
                    shown.
                  </p>
                </div>
              </div>
              <div className="mt-5 flex justify-end">
                <Button
                  disabled={
                    !scopePrincipal ||
                    !scopeRole ||
                    !scopeDatabase ||
                    !scopeTable ||
                    !scopeDimension ||
                    !scopeColumn ||
                    !scopeValues.trim() ||
                    putScope.isPending
                  }
                  onClick={() => putScope.mutate()}
                >
                  Limit rows
                </Button>
              </div>
            </Section>

            <Section
              step={3}
              title="Hide a sensitive column"
              description="People with this role see the column, but not its real values."
            >
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
                <SelectField
                  label="Role"
                  value={maskRole}
                  options={roleOptions}
                  placeholder="Select role"
                  onChange={setMaskRole}
                />
                <SelectField
                  label="Database"
                  value={maskDatabase}
                  options={databaseOptions}
                  placeholder="Select database"
                  onChange={(value) => {
                    setMaskDatabase(value);
                    setMaskTable("");
                    setMaskColumn("");
                  }}
                />
                <SelectField
                  label="Table"
                  value={maskTable}
                  options={maskTables.data ?? []}
                  placeholder={
                    maskDatabase ? "Select table" : "Select database first"
                  }
                  disabled={!maskDatabase || maskTables.isLoading}
                  onChange={(value) => {
                    setMaskTable(value);
                    setMaskColumn("");
                  }}
                />
                <SelectField
                  label="Column"
                  value={maskColumn}
                  options={maskColumns.data ?? []}
                  placeholder={
                    maskTable ? "Select column" : "Select table first"
                  }
                  disabled={!maskTable || maskColumns.isLoading}
                  onChange={setMaskColumn}
                />
                <ChoiceField
                  id="mask-type"
                  label="Show values as"
                  value={maskType}
                  options={MASK_OPTIONS}
                  onChange={setMaskType}
                />
              </div>
              <div className="mt-5 flex justify-end">
                <Button
                  disabled={
                    !maskRole ||
                    !maskDatabase ||
                    !maskTable ||
                    !maskColumn ||
                    !maskType ||
                    putMask.isPending
                  }
                  onClick={() => putMask.mutate()}
                >
                  Hide column
                </Button>
              </div>
            </Section>
          </TabsContent>

          <TabsContent value="effective" className="space-y-6 pt-5">
            <TabLead>
              See exactly what one person gets from a table when they work
              under a role: what they may do, which rows, and which columns
              are hidden.
            </TabLead>
            <Section
              title="Check a person's access"
              description="Pick a person, the role they use, and a table."
            >
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <SelectField
                  label="Person"
                  value={effectivePrincipal}
                  options={userOptions}
                  placeholder="Select user"
                  onChange={setEffectivePrincipal}
                />
                <SelectField
                  label="Using role"
                  value={effectiveRole}
                  options={roleOptions}
                  placeholder="Select role"
                  onChange={setEffectiveRole}
                />
                <SelectField
                  label="Database"
                  value={effectiveDatabase}
                  options={databaseOptions}
                  placeholder="Select database"
                  onChange={(value) => {
                    setEffectiveDatabase(value);
                    setEffectiveTable("");
                  }}
                />
                <SelectField
                  label="Table"
                  value={effectiveTable}
                  options={effectiveTables.data ?? []}
                  placeholder={
                    effectiveDatabase ? "Select table" : "Select database first"
                  }
                  disabled={!effectiveDatabase || effectiveTables.isLoading}
                  onChange={setEffectiveTable}
                />
              </div>
              <div className="mt-5 flex justify-end">
                <Button
                  disabled={
                    !effectivePrincipal ||
                    !effectiveRole ||
                    !effectiveDatabase ||
                    !effectiveTable ||
                    effective.isPending
                  }
                  onClick={() => effective.mutate()}
                >
                  Check access
                </Button>
              </div>
            </Section>

            {effective.data ? (
              <>
                <Section
                  title="What they get"
                  description={`${effective.data.principal} using ${effective.data.active_role} on ${effective.data.resource}`}
                >
                  <div className="mb-4 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                    Enforced by {effective.data.authorization_provider}
                    <StateBadge status={effective.data.policy_health} />
                  </div>
                  <SimpleTableViewport className="max-h-none">
                    <Table className="min-w-[640px]">
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-52 px-4">Rule</TableHead>
                          <TableHead className="px-4">Result</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        <TableRow>
                          <TableCell className="px-4 font-medium">
                            Allowed to
                          </TableCell>
                          <TableCell className="px-4 whitespace-normal">
                            {effective.data.object_access
                              .map((access) => PERMISSION_LABELS[access] ?? access)
                              .join(", ") || "Nothing: no access to this table"}
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="px-4 font-medium">
                            Rows shown
                          </TableCell>
                          <TableCell className="px-4 whitespace-normal">
                            {effective.data.row_restrictions.join(" AND ") ||
                              "All rows"}
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="px-4 font-medium">
                            Hidden columns
                          </TableCell>
                          <TableCell className="px-4 whitespace-normal">
                            {Object.entries(effective.data.column_restrictions)
                              .map(([column, mask]) => `${column} (${(MASK_OPTIONS.find((option) => option.value === mask)?.label ?? mask).toLowerCase()})`)
                              .join(", ") || "None"}
                          </TableCell>
                        </TableRow>
                      </TableBody>
                    </Table>
                  </SimpleTableViewport>
                </Section>

                <Section
                  title="Policies behind this result"
                  description="The rules that produced what this person gets."
                >
                  {effectivePolicies.length ? (
                    <>
                      <PolicyTable policies={visibleEffectivePolicies.items} />
                      <SimpleTablePagination
                        page={visibleEffectivePolicies.currentPage}
                        pageSize={effectivePolicyPageSize}
                        total={effectivePolicies.length}
                        onPageChange={setEffectivePolicyPage}
                        onPageSizeChange={(value) => {
                          setEffectivePolicyPageSize(value);
                          setEffectivePolicyPage(1);
                        }}
                      />
                    </>
                  ) : (
                    <EmptyState
                      title="No policy applies"
                      description="No Nova policy gives this role anything on this table."
                    />
                  )}
                </Section>
              </>
            ) : (
              <EmptyState
                title="Nothing checked yet"
                description="Pick a person, a role and a table above, then check their access."
              />
            )}
          </TabsContent>
        </Tabs>
      </Main>
    </>
  );
}
