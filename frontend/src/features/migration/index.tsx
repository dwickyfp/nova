import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/layout/header";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingOverlay } from "@/components/ui/loading-overlay";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  createSource,
  dryRun,
  enqueueMigration,
  fetchCapabilities,
  fetchEngineStatus,
  fetchMigrationJob,
  fetchPlan,
  fetchSourceDatabases,
  fetchSources,
  runPreflight,
  testSourceConnection,
  type DryRunResponse,
  type EngineStatus,
  type MigrationCapabilities,
  type MigrationJob,
  type PlanResponse,
  type PreflightResponse,
  type QueuedMigrationJob,
  type SourceConnection,
} from "./api";

type DatabaseReview = {
  database: string;
  dryRun: DryRunResponse;
  plan: PlanResponse;
  preflight: PreflightResponse;
};

type VisibleJob = MigrationJob | QueuedMigrationJob;

const FINISHED_STATUSES = new Set([
  "succeeded",
  "partial",
  "failed",
  "interrupted",
]);
const LAST_JOB_ID_KEY = "nova.migration.lastJobId";
const MAX_DATABASES = 50;
const JOB_FAILURE_MESSAGES: Record<string, string> = {
  session_expired:
    "The migration session expired. Sign in again, then review and submit a new job.",
  execute_disabled:
    "Migration execution is disabled. Ask an operator to enable it before retrying.",
  confirmation_required:
    "The confirmation was not accepted. Review the selection before retrying.",
  preflight_failed:
    "The worker preflight failed. Check target access and the transfer stage before retrying.",
  data_movement_failed:
    "Table data transfer failed. Check the transfer stage before retrying.",
  unknown_source:
    "The source connection no longer exists. Choose a source and review again.",
  source_unavailable:
    "The worker could not connect to the source. Check its address and credential reference.",
  worker_interrupted:
    "The worker stopped before the migration finished. Review the job before retrying.",
  migration_failed:
    "The worker could not complete the migration. Review the job before retrying.",
};

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function jobFailureMessage(code: string | null | undefined) {
  return code &&
    Object.prototype.hasOwnProperty.call(JOB_FAILURE_MESSAGES, code)
    ? JOB_FAILURE_MESSAGES[code]
    : JOB_FAILURE_MESSAGES.migration_failed;
}

export function MigrationPage() {
  const [capabilities, setCapabilities] =
    useState<MigrationCapabilities | null>(null);
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [engineError, setEngineError] = useState("");
  const [sources, setSources] = useState<SourceConnection[]>([]);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);

  const [sourceName, setSourceName] = useState("");
  const [sourceHost, setSourceHost] = useState("");
  const [sourcePort, setSourcePort] = useState("9030");
  const [sourceUser, setSourceUser] = useState("root");
  const [sourceSecretRef, setSourceSecretRef] = useState("");
  const [testingSource, setTestingSource] = useState(false);
  const [testedSourceKey, setTestedSourceKey] = useState("");
  const [sourceTestStatus, setSourceTestStatus] = useState<
    "idle" | "success" | "failure"
  >("idle");
  const [submittingSource, setSubmittingSource] = useState(false);
  const [selectedSource, setSelectedSource] = useState("");

  const [availableDatabases, setAvailableDatabases] = useState<string[] | null>(
    null,
  );
  const [unsupportedDatabases, setUnsupportedDatabases] = useState<string[]>(
    [],
  );
  const [selectedDatabases, setSelectedDatabases] = useState<string[]>([]);
  const [databaseSearch, setDatabaseSearch] = useState("");
  const [discovering, setDiscovering] = useState(false);
  const [discoveryError, setDiscoveryError] = useState("");

  const [reviews, setReviews] = useState<DatabaseReview[]>([]);
  const [reviewingDatabase, setReviewingDatabase] = useState("");
  const [reviewError, setReviewError] = useState("");
  const [includeData, setIncludeData] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [enqueueing, setEnqueueing] = useState(false);

  const [job, setJob] = useState<VisibleJob | null>(null);
  const [jobError, setJobError] = useState("");
  const [savedJobId, setSavedJobId] = useState(() => {
    try {
      return window.sessionStorage.getItem(LAST_JOB_ID_KEY) ?? "";
    } catch {
      return "";
    }
  });

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    setEngineError("");
    void fetchEngineStatus()
      .then((nextEngine) => setEngine(nextEngine))
      .catch((error) => {
        setEngine(null);
        setEngineError(
          errorMessage(error, "Could not check the sync utility."),
        );
      });
    try {
      const [capabilitiesResult, sourcesResult] = await Promise.allSettled([
        fetchCapabilities(),
        fetchSources(),
      ]);
      const errors: string[] = [];
      if (capabilitiesResult.status === "fulfilled") {
        setCapabilities(capabilitiesResult.value);
      } else {
        setCapabilities(null);
        errors.push(
          errorMessage(
            capabilitiesResult.reason,
            "Could not load migration capabilities.",
          ),
        );
      }
      if (sourcesResult.status === "fulfilled") {
        setSources(sourcesResult.value.connections);
      } else {
        errors.push(
          errorMessage(sourcesResult.reason, "Could not load saved sources."),
        );
      }
      setLoadError(errors.join(" "));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!savedJobId || job) return;
    let active = true;
    void fetchMigrationJob(savedJobId)
      .then((previousJob) => {
        if (active) {
          setJob(previousJob);
          setJobError("");
        }
      })
      .catch((error) => {
        if (active) {
          setJobError(
            errorMessage(error, "Could not load the previous migration job."),
          );
        }
      });
    return () => {
      active = false;
    };
  }, [savedJobId, job]);

  const jobId = job?.job_id ?? savedJobId;
  const jobStatus = job?.status;
  useEffect(() => {
    if (!jobId || !jobStatus || FINISHED_STATUSES.has(jobStatus)) return;

    let active = true;
    const timer = window.setInterval(async () => {
      try {
        const next = await fetchMigrationJob(jobId);
        if (active) {
          setJob(next);
          setJobError("");
        }
      } catch (error) {
        if (active)
          setJobError(errorMessage(error, "Could not refresh job status."));
      }
    }, 2500);

    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [jobId, jobStatus]);

  const activeJob = !!job && !FINISHED_STATUSES.has(job.status);
  const sourceKey = JSON.stringify([
    sourceName.trim(),
    sourceHost.trim(),
    sourcePort.trim(),
    sourceUser.trim(),
    sourceSecretRef.trim(),
  ]);
  const sourceTestPassed =
    sourceTestStatus === "success" && testedSourceKey === sourceKey;
  const filteredDatabases = useMemo(
    () =>
      (availableDatabases ?? []).filter((name) =>
        name.toLowerCase().includes(databaseSearch.trim().toLowerCase()),
      ),
    [availableDatabases, databaseSearch],
  );
  const confirmationPhrase = `MIGRATE ${selectedDatabases.length} DATABASES`;
  const reviewReady =
    selectedDatabases.length > 0 &&
    reviews.length === selectedDatabases.length &&
    selectedDatabases.every((database) =>
      reviews.some(
        (review) => review.database === database && review.preflight.ok,
      ),
    );

  const clearReview = () => {
    setReviews([]);
    setReviewError("");
    setAcknowledged(false);
    setConfirmation("");
    setJob(null);
    setJobError("");
    setSavedJobId("");
    try {
      window.sessionStorage.removeItem(LAST_JOB_ID_KEY);
    } catch {
      // Session storage may be unavailable in an embedded browser.
    }
  };

  const chooseSource = (name: string) => {
    setSelectedSource(name);
    setAvailableDatabases(null);
    setUnsupportedDatabases([]);
    setSelectedDatabases([]);
    setDatabaseSearch("");
    setDiscoveryError("");
    clearReview();
  };

  const discoverDatabases = async (name = selectedSource) => {
    if (!name) return;
    setDiscovering(true);
    setDiscoveryError("");
    setAvailableDatabases(null);
    setUnsupportedDatabases([]);
    setSelectedDatabases([]);
    setDatabaseSearch("");
    clearReview();
    try {
      const result = await fetchSourceDatabases(name);
      setAvailableDatabases(
        [...result.databases].sort((a, b) => a.localeCompare(b)),
      );
      setUnsupportedDatabases(result.unsupported ?? []);
    } catch (error) {
      setDiscoveryError(
        errorMessage(error, "Could not list source databases."),
      );
    } finally {
      setDiscovering(false);
    }
  };

  const clearSourceTest = () => {
    setSourceTestStatus("idle");
    setTestedSourceKey("");
  };

  const probeSource = async () => {
    const source = sourceName.trim();
    const host = sourceHost.trim();
    const port = Number(sourcePort);
    const username = sourceUser.trim();
    if (!source || !host || !username) {
      toast.error("Enter a connection name, source host, and username.");
      return;
    }
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      toast.error("Port must be between 1 and 65535.");
      return;
    }

    const testedKey = sourceKey;
    setTestingSource(true);
    clearSourceTest();
    try {
      const result = await testSourceConnection({
        source,
        host,
        port,
        username,
        secret_ref: sourceSecretRef.trim() || undefined,
      });
      if (!result.connected) throw new Error("Connection test did not succeed");
      setTestedSourceKey(testedKey);
      setSourceTestStatus("success");
    } catch {
      setSourceTestStatus("failure");
    } finally {
      setTestingSource(false);
    }
  };

  const registerSource = async () => {
    const name = sourceName.trim();
    const host = sourceHost.trim();
    const port = Number(sourcePort);
    if (!name || !host || !sourceUser.trim()) {
      toast.error("Enter a connection name, source host, and username.");
      return;
    }
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      toast.error("Port must be between 1 and 65535.");
      return;
    }
    if (!sourceTestPassed) {
      toast.error("Test the source connection before saving it.");
      return;
    }

    setSubmittingSource(true);
    try {
      const created = await createSource({
        name,
        host,
        port,
        username: sourceUser.trim(),
        secret_ref: sourceSecretRef.trim() || undefined,
      });
      setSources((current) => [...current, created]);
      setSourceName("");
      setSourceHost("");
      setSourceSecretRef("");
      clearSourceTest();
      chooseSource(created.name);
      toast.success(`Source ${created.name} registered.`);
      await discoverDatabases(created.name);
    } catch (error) {
      toast.error(errorMessage(error, "Could not register the source."));
    } finally {
      setSubmittingSource(false);
    }
  };

  const toggleDatabase = (database: string, checked: boolean) => {
    if (checked && selectedDatabases.length >= MAX_DATABASES) {
      toast.error(`Select up to ${MAX_DATABASES} databases per job.`);
      return;
    }
    setSelectedDatabases((current) =>
      checked
        ? current.includes(database)
          ? current
          : [...current, database]
        : current.filter((selected) => selected !== database),
    );
    clearReview();
  };

  const selectShown = () => {
    const newDatabases = filteredDatabases.filter(
      (database) => !selectedDatabases.includes(database),
    );
    if (selectedDatabases.length + newDatabases.length > MAX_DATABASES) {
      toast.error(
        `Select up to ${MAX_DATABASES} databases per job. Narrow the filter.`,
      );
      return;
    }
    setSelectedDatabases((current) => [...current, ...newDatabases]);
    clearReview();
  };

  const reviewSelection = async () => {
    if (!selectedSource || selectedDatabases.length === 0) return;
    const databases = [...selectedDatabases];
    setReviews([]);
    setReviewError("");
    setAcknowledged(false);
    setConfirmation("");
    setJob(null);
    setJobError("");
    setSavedJobId("");
    try {
      window.sessionStorage.removeItem(LAST_JOB_ID_KEY);
    } catch {
      // The current page can still submit and track a new job.
    }
    for (const database of databases) {
      setReviewingDatabase(database);
      try {
        const [dryRunResult, planResult] = await Promise.all([
          dryRun(selectedSource, database),
          fetchPlan({ source: selectedSource, database }),
        ]);
        const preflightResult = await runPreflight({
          source: selectedSource,
          database,
          target_database: planResult.target_database,
          include_data: includeData,
        });
        setReviews((current) => [
          ...current,
          {
            database,
            dryRun: dryRunResult,
            plan: planResult,
            preflight: preflightResult,
          },
        ]);
      } catch (error) {
        setReviewError(
          `${database}: ${errorMessage(error, "Could not prepare the migration plan.")}`,
        );
        break;
      }
    }
    setReviewingDatabase("");
  };

  const startMigration = async () => {
    if (
      !reviewReady ||
      !acknowledged ||
      confirmation !== confirmationPhrase ||
      !capabilities?.execute_available ||
      job !== null
    ) {
      return;
    }
    setEnqueueing(true);
    setJobError("");
    try {
      const queued = await enqueueMigration({
        source: selectedSource,
        databases: selectedDatabases,
        acknowledge_omissions: true,
        confirmation,
        include_data: includeData,
      });
      setJob(queued);
      setSavedJobId(queued.job_id);
      try {
        window.sessionStorage.setItem(LAST_JOB_ID_KEY, queued.job_id);
      } catch {
        // The job remains visible for the current page session.
      }
      toast.success("Migration job queued.");
    } catch (error) {
      setJobError(errorMessage(error, "Could not queue the migration."));
    } finally {
      setEnqueueing(false);
    }
  };

  const refreshJob = async () => {
    if (!jobId) return;
    try {
      setJob(await fetchMigrationJob(jobId));
      setJobError("");
    } catch (error) {
      setJobError(errorMessage(error, "Could not refresh job status."));
    }
  };

  return (
    <div data-layout="fixed" className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
      <Header fixed>
        <div className="min-w-0 flex-1">
          <h1 className="truncate font-heading text-lg">Migrate databases</h1>
          <p className="text-sm text-muted-foreground">
            Connect to a source cluster, choose databases, review the plan, then
            run the migration on a worker.
          </p>
        </div>
        <Button
          variant="outline"
          size="icon"
          className="ml-2 min-h-11 min-w-11 shrink-0"
          onClick={() => void load()}
          aria-label="Refresh migration settings"
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
        </Button>
      </Header>

      <main className="min-h-0 min-w-0 flex-1 space-y-6 overflow-auto p-4 md:p-6">
        {loading && <LoadingOverlay label="Loading migration settings" />}
        {loadError && (
          <div
            role="alert"
            className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive"
          >
            {loadError}
          </div>
        )}
        {!job && savedJobId && jobError && (
          <EmptyState
            variant="error"
            title="Could not load the previous migration job"
            description={jobError}
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <Button
                  variant="outline"
                  className="min-h-11"
                  onClick={() => void refreshJob()}
                >
                  Try again
                </Button>
                <Button
                  variant="ghost"
                  className="min-h-11"
                  onClick={clearReview}
                >
                  Dismiss job
                </Button>
              </div>
            }
          />
        )}
        {!loading && capabilities && !capabilities.execute_available && (
          <div
            role="status"
            className="flex items-start gap-3 rounded-md border border-warning/40 bg-warning/5 p-4 text-sm"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning-strong" />
            <div>
              <p className="font-medium">Migration execution is disabled</p>
              <p className="mt-1 text-muted-foreground">
                Connection tests and plan review still work. After a restorable
                target backup is ready, an operator can set{" "}
                <code>MIGRATION_EXECUTE_ENABLED=true</code> for the backend and
                worker to allow job submission.
              </p>
            </div>
          </div>
        )}

        <section
          aria-labelledby="source-heading"
          className="rounded-lg border border-surface-border bg-card p-4 md:p-6"
        >
          <div className="flex items-start gap-3">
            <span
              aria-hidden="true"
              className="mt-0.5 font-mono text-sm text-muted-foreground"
            >
              01
            </span>
            <div className="min-w-0 flex-1">
              <h2
                id="source-heading"
                className="font-heading text-base font-semibold"
              >
                Source connection
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Enter the source SQL address and a credential reference, or use
                a saved connection.
              </p>
            </div>
          </div>

          <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-[1fr_1.3fr_0.55fr_1fr_1.3fr]">
            <div className="min-w-0 space-y-2">
              <Label htmlFor="source-name">Connection name</Label>
              <Input
                id="source-name"
                className="min-h-11"
                value={sourceName}
                onChange={(event) => {
                  setSourceName(event.target.value);
                  clearSourceTest();
                }}
                placeholder="Production source"
                disabled={activeJob || !!reviewingDatabase || discovering || testingSource}
              />
            </div>
            <div className="min-w-0 space-y-2">
              <Label htmlFor="source-host">IP or hostname</Label>
              <Input
                id="source-host"
                className="min-h-11"
                value={sourceHost}
                onChange={(event) => {
                  setSourceHost(event.target.value);
                  clearSourceTest();
                }}
                placeholder="10.0.0.12"
                disabled={activeJob || !!reviewingDatabase || discovering || testingSource}
              />
            </div>
            <div className="min-w-0 space-y-2">
              <Label htmlFor="source-port">SQL port</Label>
              <Input
                id="source-port"
                type="number"
                inputMode="numeric"
                min={1}
                max={65535}
                className="min-h-11"
                value={sourcePort}
                onChange={(event) => {
                  setSourcePort(event.target.value);
                  clearSourceTest();
                }}
                disabled={activeJob || !!reviewingDatabase || discovering || testingSource}
              />
            </div>
            <div className="min-w-0 space-y-2">
              <Label htmlFor="source-user">Username</Label>
              <Input
                id="source-user"
                className="min-h-11"
                value={sourceUser}
                onChange={(event) => {
                  setSourceUser(event.target.value);
                  clearSourceTest();
                }}
                disabled={activeJob || !!reviewingDatabase || discovering || testingSource}
              />
            </div>
            <div className="min-w-0 space-y-2">
              <Label htmlFor="source-secret">Credential reference</Label>
              <Input
                id="source-secret"
                className="min-h-11"
                value={sourceSecretRef}
                onChange={(event) => {
                  setSourceSecretRef(event.target.value);
                  clearSourceTest();
                }}
                placeholder="Optional"
                disabled={activeJob || !!reviewingDatabase || discovering || testingSource}
              />
              <p className="text-xs text-muted-foreground">
                For password-protected sources, enter a configured secret
                reference. Never enter the password here.
              </p>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button
              variant="outline"
              className="min-h-11"
              onClick={() => void probeSource()}
              disabled={
                testingSource ||
                submittingSource ||
                activeJob ||
                !!reviewingDatabase ||
                discovering
              }
            >
              {testingSource && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {testingSource ? "Testing connection" : "Test connection"}
            </Button>
            <Button
              className="min-h-11"
              onClick={() => void registerSource()}
              disabled={
                !sourceTestPassed ||
                testingSource ||
                submittingSource ||
                activeJob ||
                !!reviewingDatabase ||
                discovering
              }
            >
              {submittingSource && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Save source and list databases
            </Button>
            {sourceTestStatus === "success" && sourceTestPassed && (
              <span role="status" className="text-sm text-success-strong">
                Connection successful. You can save this source.
              </span>
            )}
            {sourceTestStatus === "failure" && (
              <span role="alert" className="text-sm text-destructive">
                Could not test this connection. Check the address, account,
                credential reference, and worker, then try again.
              </span>
            )}
            {engine && (
              <span className="text-xs text-muted-foreground">
                Sync utility:{" "}
                {engine.available ? "available" : "not configured"}
              </span>
            )}
            {engineError && (
              <span
                role="status"
                className="break-words text-xs text-warning-strong"
              >
                Sync utility status unavailable: {engineError}
              </span>
            )}
          </div>

          {sources.length > 0 && (
            <div className="mt-5 border-t border-border pt-5">
              <Label htmlFor="saved-source">Saved connection</Label>
              <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-center">
                <select
                  id="saved-source"
                  value={selectedSource}
                  onChange={(event) => chooseSource(event.target.value)}
                  disabled={activeJob || discovering || !!reviewingDatabase}
                  className="min-h-11 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:max-w-sm"
                >
                  <option value="">Select a connection</option>
                  {sources.map((source) => (
                    <option key={source.id} value={source.name}>
                      {source.name} ({source.host}:{source.port})
                    </option>
                  ))}
                </select>
                <Button
                  variant="outline"
                  className="min-h-11"
                  onClick={() => void discoverDatabases()}
                  disabled={
                    !selectedSource ||
                    discovering ||
                    activeJob ||
                    !!reviewingDatabase
                  }
                >
                  {discovering && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  List databases
                </Button>
              </div>
            </div>
          )}
        </section>

        <section
          aria-labelledby="database-heading"
          className="rounded-lg border border-surface-border bg-card p-4 md:p-6"
        >
          <div className="flex items-start gap-3">
            <span
              aria-hidden="true"
              className="mt-0.5 font-mono text-sm text-muted-foreground"
            >
              02
            </span>
            <div className="min-w-0 flex-1">
              <h2
                id="database-heading"
                className="font-heading text-base font-semibold"
              >
                Choose databases
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Select every database to include in this migration job.
              </p>
            </div>
            <span className="text-sm font-medium">
              {selectedDatabases.length} of {MAX_DATABASES} selected
            </span>
          </div>

          {discovering ? (
            <LoadingOverlay
              className="mt-5"
              label={`Listing databases from ${selectedSource}`}
            />
          ) : discoveryError ? (
            <EmptyState
              className="mt-5"
              variant="error"
              title="Could not list databases"
              description={discoveryError}
              action={
                <Button
                  variant="outline"
                  className="min-h-11"
                  onClick={() => void discoverDatabases()}
                >
                  Try again
                </Button>
              }
            />
          ) : availableDatabases === null ? (
            <EmptyState
              className="mt-5"
              title="Choose a source connection"
              description="Save a source or choose a saved connection to list its databases."
            />
          ) : availableDatabases.length === 0 ? (
            <EmptyState
              className="mt-5"
              title="No selectable databases"
              description="Check the source connection and its access, then list databases again."
              action={
                <Button
                  variant="outline"
                  className="min-h-11"
                  onClick={() => void discoverDatabases()}
                >
                  List again
                </Button>
              }
            />
          ) : (
            <div className="mt-5">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div className="w-full min-w-0 space-y-2 sm:max-w-sm">
                  <Label htmlFor="database-search">Find a database</Label>
                  <Input
                    id="database-search"
                    className="min-h-11"
                    value={databaseSearch}
                    onChange={(event) => setDatabaseSearch(event.target.value)}
                    placeholder="Filter by name"
                  />
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    className="min-h-11"
                    onClick={selectShown}
                    disabled={
                      filteredDatabases.length === 0 ||
                      selectedDatabases.length >= MAX_DATABASES ||
                      filteredDatabases.every((database) =>
                        selectedDatabases.includes(database),
                      ) ||
                      activeJob ||
                      !!reviewingDatabase
                    }
                  >
                    Select shown
                  </Button>
                  <Button
                    variant="ghost"
                    className="min-h-11"
                    onClick={() => {
                      setSelectedDatabases([]);
                      clearReview();
                    }}
                    disabled={
                      selectedDatabases.length === 0 ||
                      activeJob ||
                      !!reviewingDatabase
                    }
                  >
                    Clear selection
                  </Button>
                </div>
              </div>
              {filteredDatabases.length === 0 ? (
                <EmptyState
                  className="mt-4"
                  title="No matching databases"
                  description="Try a different name or clear the filter."
                />
              ) : (
                <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {filteredDatabases.map((database) => (
                    <div
                      key={database}
                      className="flex min-h-11 min-w-0 items-center gap-3 rounded-md border border-border px-3 py-2"
                    >
                      <Checkbox
                        id={`database-${database}`}
                        checked={selectedDatabases.includes(database)}
                        onCheckedChange={(checked) =>
                          toggleDatabase(database, checked === true)
                        }
                        disabled={activeJob || !!reviewingDatabase}
                      />
                      <Label
                        htmlFor={`database-${database}`}
                        className="min-w-0 flex-1 cursor-pointer break-all py-1 font-normal"
                      >
                        {database}
                      </Label>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {availableDatabases !== null && unsupportedDatabases.length > 0 && (
            <div
              role="status"
              className="mt-4 rounded-md border border-warning/30 bg-warning/5 p-4 text-sm"
            >
              <p className="font-medium text-warning-strong">
                Some database names cannot be migrated yet
              </p>
              <p className="mt-1 text-muted-foreground">
                Names requiring quoted identifiers are excluded from this
                selection.
              </p>
              <ul className="mt-2 list-disc space-y-1 pl-5">
                {unsupportedDatabases.map((name) => (
                  <li key={name} className="break-all">
                    {name}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>

        <section
          aria-labelledby="review-heading"
          className="rounded-lg border border-surface-border bg-card p-4 md:p-6"
        >
          <div className="flex items-start gap-3">
            <span
              aria-hidden="true"
              className="mt-0.5 font-mono text-sm text-muted-foreground"
            >
              03
            </span>
            <div className="min-w-0 flex-1">
              <h2
                id="review-heading"
                className="font-heading text-base font-semibold"
              >
                Review and run
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Check object verdicts, target statements, and access for each
                selected database.
              </p>
            </div>
          </div>

          <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <label className="flex min-h-11 items-center gap-3 text-sm">
              <Checkbox
                checked={includeData}
                onCheckedChange={(checked) => {
                  setIncludeData(checked === true);
                  clearReview();
                }}
                disabled={activeJob || !!reviewingDatabase}
              />
              Include table data
            </label>
            <Button
              variant="outline"
              className="min-h-11"
              onClick={() => void reviewSelection()}
              disabled={
                selectedDatabases.length === 0 ||
                !!reviewingDatabase ||
                activeJob
              }
            >
              {reviewingDatabase && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {reviewingDatabase
                ? `Reviewing ${reviewingDatabase}`
                : `Review ${selectedDatabases.length || ""} selected database${selectedDatabases.length === 1 ? "" : "s"}`}
            </Button>
          </div>
          {includeData && (
            <p className="mt-2 text-xs text-muted-foreground">
              The source and target must share a configured transfer stage.
            </p>
          )}
          {reviewError && (
            <p role="alert" className="mt-4 text-sm text-destructive">
              {reviewError}
            </p>
          )}
          {selectedDatabases.length === 0 && (
            <p className="mt-4 text-sm text-muted-foreground">
              Choose at least one database to prepare a plan.
            </p>
          )}

          {reviews.length > 0 && (
            <div className="mt-5 space-y-4">
              {reviews.map((review) => (
                <div
                  key={review.database}
                  className="min-w-0 rounded-md border border-surface-border bg-surface-1 p-4"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="min-w-0 break-all font-medium">
                      {review.database}
                    </h3>
                    <StatusBadge
                      tone={review.preflight.ok ? "success" : "danger"}
                    >
                      {review.preflight.ok
                        ? "Preflight passed"
                        : "Preflight failed"}
                    </StatusBadge>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
                    <span>{review.plan.step_count} plan steps</span>
                    <span>{review.dryRun.summary.lossy} lossy</span>
                    <span>{review.dryRun.summary.skipped} skipped</span>
                    <span>{review.plan.blocked.length} blocked</span>
                  </div>
                  {!review.preflight.ok && (
                    <div className="mt-3 text-sm text-destructive">
                      <p>Resolve these checks before submitting:</p>
                      <ul className="mt-1 list-disc pl-5">
                        {review.preflight.checks
                          .filter((check) => !check.satisfied)
                          .map((check) => (
                            <li key={check.privilege}>
                              {check.privilege}: {check.reason}
                            </li>
                          ))}
                        {review.preflight.storage_ok === false && (
                          <li>{review.preflight.storage_reason}</li>
                        )}
                      </ul>
                    </div>
                  )}
                  {(review.dryRun.items.some(
                    (item) => item.verdict !== "migratable",
                  ) ||
                    review.plan.blocked.length > 0) && (
                    <div className="mt-3 text-sm">
                      <p className="font-medium">Objects needing attention</p>
                      <ul className="mt-1 space-y-1 text-muted-foreground">
                        {review.dryRun.items
                          .filter((item) => item.verdict !== "migratable")
                          .map((item) => (
                            <li
                              key={`${item.kind}:${item.name}`}
                              className="break-words"
                            >
                              {item.name} ({item.verdict}): {item.reason}
                            </li>
                          ))}
                        {review.plan.blocked.map((item) => (
                          <li
                            key={`blocked:${item.kind}:${item.name}`}
                            className="break-words"
                          >
                            {item.name} (blocked): {item.reason}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <details className="mt-3 text-sm">
                    <summary className="min-h-11 cursor-pointer py-2 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      View target statements for {review.database}
                    </summary>
                    {review.plan.steps.length === 0 ? (
                      <p className="py-2 text-muted-foreground">
                        No executable statements in this plan.
                      </p>
                    ) : (
                      <ol className="space-y-3 border-t border-border pt-3">
                        {review.plan.steps.map((step) => (
                          <li key={step.order} className="min-w-0">
                            <p className="font-medium">
                              {step.order}. {step.kind.replace(/_/g, " ")}:{" "}
                              {step.object_name}
                            </p>
                            <code className="mt-1 block whitespace-pre-wrap break-all rounded bg-background p-2 text-xs">
                              {step.statement}
                            </code>
                          </li>
                        ))}
                      </ol>
                    )}
                  </details>
                </div>
              ))}
            </div>
          )}

          {reviews.length === selectedDatabases.length &&
            reviews.length > 0 && (
              <div className="mt-6 border-t border-border pt-5">
                <label className="flex min-h-11 items-start gap-3 text-sm">
                  <Checkbox
                    className="mt-1"
                    checked={acknowledged}
                    onCheckedChange={(checked) =>
                      setAcknowledged(checked === true)
                    }
                    disabled={!reviewReady || activeJob}
                  />
                  <span>
                    I reviewed every plan and any omissions. A restorable backup
                    exists for the target cluster.
                  </span>
                </label>
                <div className="mt-4 max-w-sm space-y-2">
                  <Label htmlFor="migration-confirmation">
                    Type {confirmationPhrase} to confirm
                  </Label>
                  <Input
                    id="migration-confirmation"
                    className="min-h-11 font-mono"
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                    autoComplete="off"
                    disabled={!reviewReady || activeJob}
                  />
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <Button
                    className="min-h-11"
                    onClick={() => void startMigration()}
                    disabled={
                      !reviewReady ||
                      !acknowledged ||
                      confirmation !== confirmationPhrase ||
                      !capabilities?.execute_available ||
                      enqueueing ||
                      !!job
                    }
                  >
                    {enqueueing && (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    )}
                    Queue migration job
                  </Button>
                  {!reviewReady && (
                    <p className="text-sm text-destructive">
                      All selected databases must pass preflight.
                    </p>
                  )}
                  {job && FINISHED_STATUSES.has(job.status) && (
                    <p className="text-sm text-muted-foreground">
                      Review the selection again to start another job.
                    </p>
                  )}
                </div>
                {jobError && (
                  <p role="alert" className="mt-3 text-sm text-destructive">
                    {jobError}
                  </p>
                )}
              </div>
            )}
        </section>

        {job && (
          <section
            aria-labelledby="job-heading"
            className="rounded-lg border border-surface-border bg-card p-4 md:p-6"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2
                  id="job-heading"
                  className="font-heading text-base font-semibold"
                >
                  Migration job
                </h2>
                <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                  {job.job_id}
                </p>
              </div>
              <StatusBadge
                tone={
                  job.status === "failed" || job.status === "interrupted"
                    ? "danger"
                    : job.status === "succeeded"
                      ? "success"
                      : "warning"
                }
              >
                {job.status}
              </StatusBadge>
            </div>
            {"current_database" in job && job.current_database && (
              <p role="status" className="mt-3 text-sm">
                Currently migrating {job.current_database}
              </p>
            )}
            {"results" in job &&
              (job.error_code ||
                ((job.status === "failed" || job.status === "interrupted") &&
                  job.results.length === 0)) && (
                <p
                  role="alert"
                  className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
                >
                  {jobFailureMessage(job.error_code)}
                </p>
              )}
            <div className="mt-4 space-y-2">
              {job.databases.map((database) => {
                const result =
                  "results" in job
                    ? job.results.find((entry) => entry.database === database)
                    : undefined;
                return (
                  <div
                    key={database}
                    className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-t border-border py-2 text-sm"
                  >
                    <span className="min-w-0 break-all font-medium">
                      {database}
                    </span>
                    <span
                      className={
                        result?.status === "failed"
                          ? "text-destructive"
                          : "text-muted-foreground"
                      }
                    >
                      {result
                        ? `${result.status}, ${result.succeeded} steps, ${result.rows_moved} rows`
                        : job.status === "failed" ||
                            job.status === "interrupted"
                          ? "Not completed"
                          : "Pending"}
                    </span>
                    {result?.error && (
                      <p className="w-full break-words text-destructive">
                        {result.error}
                      </p>
                    )}
                    {result &&
                      (result.steps.length > 0 || result.data.length > 0) && (
                        <details className="w-full">
                          <summary className="min-h-11 cursor-pointer py-2 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                            View results for {database}
                          </summary>
                          {result.steps.length > 0 && (
                            <ol className="space-y-2 border-t border-border pt-3">
                              {result.steps.map((step) => (
                                <li
                                  key={`${step.order}:${step.object_name}`}
                                  className="break-words"
                                >
                                  {step.order}. {step.kind.replace(/_/g, " ")}{" "}
                                  {step.object_name}: {step.status}
                                  {step.error && (
                                    <span className="block text-destructive">
                                      {step.error}
                                    </span>
                                  )}
                                </li>
                              ))}
                            </ol>
                          )}
                          {result.data.length > 0 && (
                            <ul className="mt-3 space-y-2 border-t border-border pt-3">
                              {result.data.map((table) => (
                                <li key={table.table} className="break-words">
                                  {table.table}: {table.rows_imported} of{" "}
                                  {table.rows_exported} rows imported,{" "}
                                  {table.verified
                                    ? "verified"
                                    : "verification failed"}
                                  {table.errors.length > 0 && (
                                    <span className="block text-destructive">
                                      {table.errors.join("; ")}
                                    </span>
                                  )}
                                </li>
                              ))}
                            </ul>
                          )}
                        </details>
                      )}
                  </div>
                );
              })}
            </div>
            <Button
              variant="outline"
              className="mt-4 min-h-11"
              onClick={() => void refreshJob()}
            >
              Refresh job status
            </Button>
            {jobError && (
              <p role="alert" className="mt-3 text-sm text-destructive">
                {jobError}
              </p>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
