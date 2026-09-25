import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronsUpDown,
  CircleAlert,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { toast } from "sonner";
import {
  SimpleTablePagination,
  SimpleTableToolbar,
  SimpleTableViewport,
} from "@/components/data-table/simple-table-controls";
import { Header } from "@/components/layout/header";
import { Main } from "@/components/layout/main";
import { Search } from "@/components/search";
import { Badge } from "@/components/ui/badge";
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
  enabled: boolean;
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
const MASK_OPTIONS = [
  "MASK",
  "MASK_SHOW_LAST_4",
  "MASK_SHOW_FIRST_4",
  "MASK_HASH",
  "MASK_NULL",
];

function policyKind(policy: Policy) {
  if (policy.policyType === 1) return "Masking";
  if (policy.policyType === 2) return "Row filter";
  return "Permission";
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
  placeholder,
  onChange,
}: {
  label: string;
  values: string[];
  options: string[];
  placeholder: string;
  onChange: (values: string[]) => void;
}) {
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
            aria-expanded={open}
            className="h-9 w-full justify-between px-3 font-normal"
          >
            <span
              className={cn(
                "truncate",
                !values.length && "text-muted-foreground",
              )}
            >
              {values.length ? values.join(", ") : placeholder}
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
                      value={option}
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
                      {option}
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

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-lg border bg-background">
      <div className="border-b px-5 py-4 sm:px-6">
        <h2 className="text-base font-medium">{title}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{description}</p>
      </div>
      <div className="px-5 py-5 sm:px-6 sm:py-6">{children}</div>
    </section>
  );
}

function PolicyTable({ policies }: { policies: Policy[] }) {
  return (
    <SimpleTableViewport className="max-h-none">
      <Table className="min-w-[760px]">
        <TableHeader>
          <TableRow>
            <TableHead className="w-[30%] px-4">Policy</TableHead>
            <TableHead className="w-36">Type</TableHead>
            <TableHead>Resource</TableHead>
            <TableHead className="w-[22%] px-4">Roles</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {policies.map((policy) => (
            <TableRow key={policy.id ?? policy.name}>
              <TableCell className="px-4 font-medium">{policy.name}</TableCell>
              <TableCell>
                <Badge variant="outline">{policyKind(policy)}</Badge>
              </TableCell>
              <TableCell className="max-w-md whitespace-normal text-muted-foreground">
                {policyResource(policy) || "All managed resources"}
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
          description="Manage Nova authorization roles and policies enforced by Ranger."
          actions={
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
          }
        />

        <section
          aria-label="Authorization status"
          className="grid overflow-hidden rounded-lg border bg-background md:grid-cols-3"
        >
          <div className="flex items-center gap-3 px-5 py-4 md:border-r">
            {state?.healthy ? (
              <ShieldCheck className="size-5 text-success-strong" />
            ) : (
              <CircleAlert className="size-5 text-warning-strong" />
            )}
            <div>
              <p className="text-xs text-muted-foreground">
                Authorization provider
              </p>
              <div className="mt-0.5 flex items-center gap-2">
                <span className="text-sm font-medium">Ranger</span>
                <Badge variant={state?.healthy ? "default" : "secondary"}>
                  {state?.healthy ? "Healthy" : state?.reason || "Unavailable"}
                </Badge>
              </div>
            </div>
          </div>
          <div className="border-t px-5 py-4 md:border-t-0 md:border-r">
            <p className="text-xs text-muted-foreground">Pending propagation</p>
            <p className="mt-1 text-lg font-semibold tabular-nums">
              {state?.pending_propagation ?? 0}
            </p>
          </div>
          <div className="border-t px-5 py-4 md:border-t-0">
            <p className="text-xs text-muted-foreground">Detected drift</p>
            <p className="mt-1 text-lg font-semibold tabular-nums">
              {state?.drift_count ?? 0}
            </p>
          </div>
        </section>

        <Tabs defaultValue="roles" className="gap-0">
          <TabsList className="h-auto w-full justify-start rounded-none border-b bg-transparent p-0">
            {[
              ["roles", "Roles"],
              ["policies", "Policies"],
              ["data-access", "Data access"],
              ["effective", "Effective access"],
            ].map(([value, label]) => (
              <TabsTrigger
                key={value}
                value={value}
                className="h-10 flex-none rounded-none border-0 border-b-2 border-transparent px-4 shadow-none data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
              >
                {label}
              </TabsTrigger>
            ))}
          </TabsList>

          <TabsContent value="roles" className="space-y-6 pt-6">
            <Section
              title="Authorization roles"
              description="Roles synchronized between StarRocks session markers and Ranger authorization."
            >
              <div className="space-y-4">
                <SimpleTableToolbar
                  search={roleSearch}
                  onSearchChange={(value) => {
                    setRoleSearch(value);
                    setRolePage(1);
                  }}
                  searchPlaceholder="Search roles or members..."
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
                              Members
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
                                {item.description || "Managed by Nova"}
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
                        ? "Try a different role or member name."
                        : "Create the first role below to begin assigning access."
                    }
                  />
                )}
              </div>
            </Section>

            <Section
              title="Create role"
              description="Create the synchronized StarRocks marker and Ranger role in one action."
            >
              <div className="grid gap-4 lg:grid-cols-2">
                <TextField
                  id="role-name"
                  label="Name"
                  value={newRole}
                  onChange={setNewRole}
                />
                <TextField
                  id="role-description"
                  label="Description"
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

          <TabsContent value="policies" className="pt-6">
            <Section
              title="Nova-managed policies"
              description="Review object permissions, row filters, and column masks accepted by Ranger."
            >
              <div className="space-y-4">
                <SimpleTableToolbar
                  search={policySearch}
                  onSearchChange={(value) => {
                    setPolicySearch(value);
                    setPolicyPage(1);
                  }}
                  searchPlaceholder="Search policies, roles, or resources..."
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
                        : "Policies created from Data access will appear here."
                    }
                  />
                )}
              </div>
            </Section>
          </TabsContent>

          <TabsContent value="data-access" className="space-y-6 pt-6">
            <Section
              title="Grant permission"
              description="Grant one role access to a table. Every selector is loaded from current metadata."
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
                  label="Permissions"
                  values={accesses}
                  options={PERMISSION_OPTIONS}
                  placeholder="Select permissions"
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
                  Grant permission
                </Button>
              </div>
            </Section>

            <Section
              title="Assign data scope"
              description="Bind a user's allowed values to one role and table. Comma-separated values are combined with OR."
            >
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <SelectField
                  label="User"
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
                  label="Dimension"
                  value={scopeDimension}
                  options={scopeColumns.data ?? []}
                  placeholder={
                    scopeTable ? "Select dimension" : "Select table first"
                  }
                  disabled={!scopeTable || scopeColumns.isLoading}
                  onChange={(value) => {
                    setScopeDimension(value);
                    if (!scopeColumn) setScopeColumn(value);
                  }}
                />
                <SelectField
                  label="Column"
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
                    label="Values"
                    value={scopeValues}
                    placeholder="Jakarta, Bandung"
                    onChange={setScopeValues}
                  />
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
                  Assign scope
                </Button>
              </div>
            </Section>

            <Section
              title="Mask column"
              description="Apply a Ranger masking rule to one role and column."
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
                <SelectField
                  label="Mask type"
                  value={maskType}
                  options={MASK_OPTIONS}
                  placeholder="Select mask type"
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
                  Apply mask
                </Button>
              </div>
            </Section>
          </TabsContent>

          <TabsContent value="effective" className="space-y-6 pt-6">
            <Section
              title="Effective access explorer"
              description="Inspect the resolved Ranger state for one user, active role, and table."
            >
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <SelectField
                  label="User"
                  value={effectivePrincipal}
                  options={userOptions}
                  placeholder="Select user"
                  onChange={setEffectivePrincipal}
                />
                <SelectField
                  label="Active role"
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
                  Inspect access
                </Button>
              </div>
            </Section>

            {effective.data ? (
              <>
                <Section
                  title="Resolved access"
                  description={`${effective.data.principal} using ${effective.data.active_role} on ${effective.data.resource}`}
                >
                  <div className="mb-4 flex flex-wrap gap-2">
                    <Badge>{effective.data.authorization_provider}</Badge>
                    <Badge variant="secondary">
                      {effective.data.policy_health}
                    </Badge>
                  </div>
                  <SimpleTableViewport className="max-h-none">
                    <Table className="min-w-[640px]">
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-52 px-4">Control</TableHead>
                          <TableHead className="px-4">
                            Effective value
                          </TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        <TableRow>
                          <TableCell className="px-4 font-medium">
                            Object permissions
                          </TableCell>
                          <TableCell className="px-4 whitespace-normal">
                            {effective.data.object_access.join(", ") ||
                              "Denied"}
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="px-4 font-medium">
                            Row filters
                          </TableCell>
                          <TableCell className="px-4 whitespace-normal">
                            {effective.data.row_restrictions.join(" AND ") ||
                              "None"}
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="px-4 font-medium">
                            Column masks
                          </TableCell>
                          <TableCell className="px-4 whitespace-normal">
                            {Object.entries(effective.data.column_restrictions)
                              .map(([column, mask]) => `${column}: ${mask}`)
                              .join(", ") || "None"}
                          </TableCell>
                        </TableRow>
                      </TableBody>
                    </Table>
                  </SimpleTableViewport>
                </Section>

                <Section
                  title="Matching policies"
                  description="Policies contributing to this effective access result."
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
                      title="No matching managed policies"
                      description="The selected user, role, and table have no contributing Nova-managed policy."
                    />
                  )}
                </Section>
              </>
            ) : (
              <EmptyState
                title="Choose an access context"
                description="Select a user, role, database, and table above to inspect effective access."
              />
            )}
          </TabsContent>
        </Tabs>
      </Main>
    </>
  );
}
