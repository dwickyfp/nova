import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  Ban,
  ListChecks,
  Loader2,
  Play,
  RefreshCw,
  Search,
} from 'lucide-react'
import { toast } from 'sonner'
import { Header } from '@/components/layout/header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import {
  dryRun,
  enumerateObjects,
  executeMigration,
  fetchCapabilities,
  fetchEngineStatus,
  fetchPlan,
  fetchSources,
  runPreflight,
  createSource,
  type DryRunResponse,
  type EngineStatus,
  type ExecuteResponse,
  type MigrationCapabilities,
  type MigrationVerdict,
  type PlanResponse,
  type PreflightResponse,
  type SourceConnection,
  type SourceObject,
} from './api'

const VERDICT_STYLES: Record<MigrationVerdict, string> = {
  migratable: 'bg-success/10 text-success-strong border-success/25',
  lossy: 'bg-warning/10 text-warning-strong border-warning/25',
  skipped: 'bg-destructive/10 text-destructive border-destructive/25',
}

export function MigrationPage() {
  const [capabilities, setCapabilities] = useState<MigrationCapabilities | null>(null)
  const [engine, setEngine] = useState<EngineStatus | null>(null)
  const [sources, setSources] = useState<SourceConnection[]>([])
  const [sourceName, setSourceName] = useState('')
  const [sourceHost, setSourceHost] = useState('')
  const [sourcePort, setSourcePort] = useState('9030')
  const [sourceUser, setSourceUser] = useState('root')
  const [sourceSecretRef, setSourceSecretRef] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const [selectedSource, setSelectedSource] = useState('')
  const [database, setDatabase] = useState('')
  const [objects, setObjects] = useState<SourceObject[]>([])
  const [enumerating, setEnumerating] = useState(false)
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [planResult, setPlanResult] = useState<PlanResponse | null>(null)
  const [planning, setPlanning] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const [includeData, setIncludeData] = useState(false)
  const [executing, setExecuting] = useState(false)
  const [executeResult, setExecuteResult] = useState<ExecuteResponse | null>(null)
  const [preflightResult, setPreflightResult] = useState<PreflightResponse | null>(null)
  const [checking, setChecking] = useState(false)

  const load = useCallback(async () => {
    try {
      const [caps, status, list] = await Promise.all([
        fetchCapabilities(),
        fetchEngineStatus(),
        fetchSources(),
      ])
      setCapabilities(caps)
      setEngine(status)
      setSources(list.connections)
      setSelectedSource((current) =>
        current || list.connections[0]?.name || ''
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to load migration status')
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleAddSource = async () => {
    if (!sourceName.trim() || !sourceHost.trim()) {
      toast.error('Source name and host are required')
      return
    }
    setSubmitting(true)
    try {
      await createSource({
        name: sourceName.trim(),
        host: sourceHost.trim(),
        port: Number(sourcePort) || 9030,
        username: sourceUser.trim() || 'root',
        secret_ref: sourceSecretRef.trim() || undefined,
      })
      toast.success(`Source "${sourceName}" registered`)
      setSourceName('')
      setSourceHost('')
      setSourceSecretRef('')
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to register source')
    } finally {
      setSubmitting(false)
    }
  }

  const handleEnumerate = async () => {
    if (!selectedSource) {
      toast.error('Select a source first')
      return
    }
    if (!database.trim()) {
      toast.error('Database name is required')
      return
    }
    setEnumerating(true)
    setDryRunResult(null)
    try {
      const res = await enumerateObjects(selectedSource, database.trim())
      setObjects(res.objects)
      toast.success(`${res.count} objects found in ${res.database}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to enumerate objects')
    } finally {
      setEnumerating(false)
    }
  }

  const handleDryRun = async () => {
    if (!selectedSource) {
      toast.error('Select a source first')
      return
    }
    if (!database.trim()) {
      toast.error('Database name is required')
      return
    }
    setRunning(true)
    try {
      const res = await dryRun(selectedSource, database.trim())
      setDryRunResult(res)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Dry-run failed')
    } finally {
      setRunning(false)
    }
  }

  const handlePlan = async () => {
    if (!selectedSource) {
      toast.error('Select a source first')
      return
    }
    if (!database.trim()) {
      toast.error('Database name is required')
      return
    }
    setPlanning(true)
    try {
      const res = await fetchPlan({
        source: selectedSource,
        database: database.trim(),
      })
      setPlanResult(res)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Plan failed')
    } finally {
      setPlanning(false)
    }
  }

  const handlePreflight = async () => {
    if (!selectedSource || !database.trim()) {
      toast.error('Select a source and a database first')
      return
    }
    setChecking(true)
    setPreflightResult(null)
    try {
      const target = planResult?.target_database || database.trim()
      const res = await runPreflight({
        source: selectedSource,
        database: database.trim(),
        target_database: target,
        include_data: includeData,
      })
      setPreflightResult(res)
      if (res.ok) {
        toast.success('Preflight passed')
      } else {
        toast.error('Preflight found problems')
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Preflight failed')
    } finally {
      setChecking(false)
    }
  }

  const handleExecute = async () => {
    if (!selectedSource || !database.trim()) {
      toast.error('Select a source and a database first')
      return
    }
    if (!acknowledged) {
      toast.error('Acknowledge the plan before executing')
      return
    }
    setExecuting(true)
    setExecuteResult(null)
    try {
      const target = planResult?.target_database || database.trim()
      const res = await executeMigration({
        source: selectedSource,
        database: database.trim(),
        target_database: target,
        acknowledge_omissions: true,
        confirmation: target,
        include_data: includeData,
      })
      setExecuteResult(res)
      if (res.failed > 0) {
        toast.error(`${res.failed} step(s) failed`)
      } else if (includeData) {
        toast.success(
          `Migration applied: ${res.succeeded} step(s), ${res.rows_moved} rows moved`
        )
      } else {
        toast.success(`Migration applied: ${res.succeeded} step(s)`)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Execute failed')
    } finally {
      setExecuting(false)
    }
  }

  return (
    <div className='flex h-full min-h-0 flex-col'>
      <Header fixed>
        <div className='flex min-w-0 flex-1 items-center gap-3'>
          <div className='min-w-0'>
            <h1 className='truncate text-lg font-heading'>Migration Connector</h1>
            <p className='text-sm text-muted-foreground'>
              Assess a source StarRocks cluster, preview a dry-run, and build an
              apply plan. Execution is gated on the backend.
            </p>
          </div>
        </div>
        <div className='ml-auto flex items-center gap-2'>
          <Button
            variant='outline'
            size='icon'
            className='h-8 w-8'
            onClick={load}
            aria-label='Refresh'
          >
            <RefreshCw className='h-3.5 w-3.5' />
          </Button>
        </div>
      </Header>

      <div className='min-h-0 flex-1 space-y-4 overflow-auto p-4'>
        <div className='rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm'>
          <div className='flex items-center gap-2 font-medium text-destructive'>
            <Ban className='h-4 w-4' />
            {capabilities?.execute_available
              ? 'Execute is enabled — a restorable backup is required'
              : 'Execute is disabled'}
          </div>
          <p className='mt-1 text-muted-foreground'>
            {capabilities?.execute_available
              ? 'The backend gate is open. Run a plan, review every blocked object, then execute — statements run as you, and StarRocks RBAC decides.'
              : 'The backend refuses execution until the operator opens the gate (set MIGRATION_EXECUTE_ENABLED) after issue ' +
                (capabilities?.execute_gate.issue ?? '#7') +
                ' (' +
                (capabilities?.execute_gate.name ?? 'backup/restore') +
                ') is satisfied.'}
          </p>
        </div>

        <section className='rounded-lg border border-border bg-background p-4'>
          <h2 className='text-sm font-semibold'>1. Engine</h2>
          <p className='mt-1 text-xs text-muted-foreground'>
            Nova never bundles the migration binary. The operator installs it and
            configures its path.
          </p>
          <div className='mt-2 flex items-center gap-2 text-sm'>
            <Badge variant={engine?.available ? 'secondary' : 'outline'}>
              {engine?.available ? 'Available' : 'Not configured'}
            </Badge>
            {engine?.reason && (
              <span className='text-muted-foreground'>{engine.reason}</span>
            )}
          </div>
        </section>

        <section className='rounded-lg border border-border bg-background p-4'>
          <h2 className='text-sm font-semibold'>2. Source clusters</h2>
          <p className='mt-1 text-xs text-muted-foreground'>
            Register the source StarRocks cluster to assess. Only the address is
            stored; the password lives behind a secret reference.
          </p>
          <div className='mt-3 flex flex-wrap items-end gap-2'>
            <div className='space-y-1.5'>
              <Label htmlFor='source-name'>Name</Label>
              <Input
                id='source-name'
                value={sourceName}
                onChange={(e) => setSourceName(e.target.value)}
                placeholder='prod-source'
                className='h-8 w-40'
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='source-host'>Host</Label>
              <Input
                id='source-host'
                value={sourceHost}
                onChange={(e) => setSourceHost(e.target.value)}
                placeholder='source.internal'
                className='h-8 w-44'
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='source-port'>Port</Label>
              <Input
                id='source-port'
                value={sourcePort}
                onChange={(e) => setSourcePort(e.target.value)}
                placeholder='9030'
                className='h-8 w-20'
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='source-user'>User</Label>
              <Input
                id='source-user'
                value={sourceUser}
                onChange={(e) => setSourceUser(e.target.value)}
                placeholder='root'
                className='h-8 w-28'
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='source-secret'>Secret ref</Label>
              <Input
                id='source-secret'
                value={sourceSecretRef}
                onChange={(e) => setSourceSecretRef(e.target.value)}
                placeholder='optional'
                className='h-8 w-44'
              />
            </div>
            <Button size='sm' className='h-8' onClick={handleAddSource} disabled={submitting}>
              {submitting ? <Loader2 className='mr-1.5 h-3.5 w-3.5 animate-spin' /> : null}
              Register
            </Button>
          </div>
          {sources.length > 0 && (
            <ul className='mt-3 space-y-1 text-sm'>
              {sources.map((source) => (
                <li key={source.id} className='flex items-center gap-2'>
                  <span className='font-medium'>{source.name}</span>
                  <span className='text-muted-foreground'>
                    → {source.host}:{source.port} ({source.username})
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className='rounded-lg border border-border bg-background p-4'>
          <h2 className='text-sm font-semibold'>3. Enumerate &amp; dry-run</h2>
          <div className='mt-3 flex flex-wrap items-end gap-2'>
            <div className='space-y-1.5'>
              <Label htmlFor='migration-source'>Source</Label>
              <select
                id='migration-source'
                value={selectedSource}
                onChange={(e) => setSelectedSource(e.target.value)}
                className='h-8 w-48 rounded-md border border-input bg-background px-2 text-sm'
              >
                <option value=''>Select a source…</option>
                {sources.map((source) => (
                  <option key={source.id} value={source.name}>
                    {source.name}
                  </option>
                ))}
              </select>
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='migration-db'>Database</Label>
              <Input
                id='migration-db'
                value={database}
                onChange={(e) => setDatabase(e.target.value)}
                placeholder='analytics'
                className='h-8 w-56'
              />
            </div>
            <Button
              variant='outline'
              size='sm'
              className='h-8'
              onClick={handleEnumerate}
              disabled={enumerating}
            >
              {enumerating ? (
                <Loader2 className='mr-1.5 h-3.5 w-3.5 animate-spin' />
              ) : (
                <Search className='mr-1.5 h-3.5 w-3.5' />
              )}
              Enumerate
            </Button>
            <Button size='sm' className='h-8' onClick={handleDryRun} disabled={running}>
              {running ? (
                <Loader2 className='mr-1.5 h-3.5 w-3.5 animate-spin' />
              ) : (
                <Play className='mr-1.5 h-3.5 w-3.5' />
              )}
              Dry-run
            </Button>
            <Button
              variant='outline'
              size='sm'
              className='h-8'
              onClick={handlePlan}
              disabled={planning}
            >
              {planning ? (
                <Loader2 className='mr-1.5 h-3.5 w-3.5 animate-spin' />
              ) : (
                <ListChecks className='mr-1.5 h-3.5 w-3.5' />
              )}
              Plan
            </Button>
          </div>

          {objects.length > 0 && !dryRunResult && (
            <p className='mt-3 text-xs text-muted-foreground'>
              {objects.length} objects enumerated.
            </p>
          )}

          {dryRunResult && (
            <>
              <div className='mt-3 flex flex-wrap gap-2 text-sm'>
                <Badge variant='secondary'>
                  Migratable {dryRunResult.summary.migratable}
                </Badge>
                <Badge variant='outline'>
                  Lossy {dryRunResult.summary.lossy}
                </Badge>
                <Badge variant='outline'>
                  Skipped {dryRunResult.summary.skipped}
                </Badge>
              </div>

              <div className='mt-3 min-h-0 overflow-x-auto rounded-lg border border-border'>
                <table className='w-full text-sm'>
                  <thead className='bg-muted'>
                    <tr className='text-left text-xs text-muted-foreground'>
                      <th className='px-3 py-2 font-medium'>Object</th>
                      <th className='px-3 py-2 font-medium'>Kind</th>
                      <th className='px-3 py-2 font-medium'>Verdict</th>
                      <th className='px-3 py-2 font-medium'>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dryRunResult.items.map((item) => (
                      <tr key={`${item.kind}:${item.name}`} className='border-t border-border'>
                        <td className='px-3 py-2 font-medium'>{item.name}</td>
                        <td className='px-3 py-2 capitalize text-muted-foreground'>
                          {item.kind.replace(/_/g, ' ')}
                        </td>
                        <td className='px-3 py-2'>
                          <span
                            className={`rounded px-1.5 py-0.5 text-xs font-medium capitalize ${VERDICT_STYLES[item.verdict]}`}
                          >
                            {item.verdict}
                          </span>
                        </td>
                        <td className='px-3 py-2 text-muted-foreground'>{item.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {dryRunResult && dryRunResult.summary.skipped > 0 && (
            <div className='mt-3 flex items-start gap-2 rounded-md border border-warning/30 bg-warning/5 p-2 text-xs text-warning-strong'>
              <AlertTriangle className='mt-0.5 h-3.5 w-3.5 shrink-0' />
              <span>
                Skipped objects are not migrated — masking and row-access policies
                have no DDL export in StarRocks 4.1.4. Review them before any
                future cutover.
              </span>
            </div>
          )}
        </section>

        {planResult && (
          <section className='rounded-lg border border-border bg-background p-4'>
            <h2 className='text-sm font-semibold'>4. Apply plan</h2>
            <p className='mt-1 text-xs text-muted-foreground'>
              The exact statements a cutover would run, in dependency order.
              Read-only — nothing is executed. Applying is gated on{' '}
              {capabilities?.execute_gate.issue ?? '#7'}.
            </p>

            <div className='mt-3 flex flex-wrap gap-2 text-sm'>
              <Badge variant='secondary'>{planResult.step_count} steps</Badge>
              <Badge variant='outline'>
                target: {planResult.target_database}
              </Badge>
              {planResult.blocked.length > 0 && (
                <Badge variant='outline'>
                  {planResult.blocked.length} blocked
                </Badge>
              )}
            </div>

            <div className='mt-3 min-h-0 overflow-x-auto rounded-lg border border-border'>
              <table className='w-full text-sm'>
                <thead className='bg-muted'>
                  <tr className='text-left text-xs text-muted-foreground'>
                    <th className='px-3 py-2 font-medium'>#</th>
                    <th className='px-3 py-2 font-medium'>Kind</th>
                    <th className='px-3 py-2 font-medium'>Object</th>
                    <th className='px-3 py-2 font-medium'>Statement</th>
                  </tr>
                </thead>
                <tbody>
                  {planResult.steps.map((step) => (
                    <tr key={step.order} className='border-t border-border'>
                      <td className='px-3 py-2 text-muted-foreground'>{step.order}</td>
                      <td className='px-3 py-2 capitalize text-muted-foreground'>
                        {step.kind.replace(/_/g, ' ')}
                      </td>
                      <td className='px-3 py-2 font-medium'>{step.object_name}</td>
                      <td className='px-3 py-2'>
                        <code className='block max-w-xl truncate font-mono text-xs'>
                          {step.statement}
                        </code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {planResult.blocked.length > 0 && (
              <div className='mt-3 flex items-start gap-2 rounded-md border border-warning/30 bg-warning/5 p-2 text-xs text-warning-strong'>
                <AlertTriangle className='mt-0.5 h-3.5 w-3.5 shrink-0' />
                <div className='space-y-1'>
                  <span>Not executed (no usable definition):</span>
                  <ul className='list-disc pl-4'>
                    {planResult.blocked.map((obj) => (
                      <li key={`${obj.kind}:${obj.name}`}>
                        <span className='font-medium'>{obj.name}</span> — {obj.reason}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            )}

            <div className='mt-4 border-t border-border pt-3'>
              <label className='flex items-start gap-2 text-sm'>
                <Checkbox
                  checked={includeData}
                  onCheckedChange={(v) => setIncludeData(v === true)}
                  className='mt-0.5'
                />
                <span className='text-muted-foreground'>
                  Also copy table data. Requires the source and target to reach
                  the same object storage (the transfer stage).
                </span>
              </label>
              <label className='mt-3 flex items-start gap-2 text-sm'>
                <Checkbox
                  checked={acknowledged}
                  onCheckedChange={(v) => setAcknowledged(v === true)}
                  className='mt-0.5'
                />
                <span className='text-muted-foreground'>
                  I have reviewed this plan, including any blocked or lossy
                  objects, and a restorable backup exists. I understand this
                  applies the statements above to{' '}
                  <span className='font-medium text-foreground'>
                    {planResult.target_database}
                  </span>
                  .
                </span>
              </label>
              <div className='mt-3 flex items-center gap-2'>
                <Button
                  size='sm'
                  className='h-8'
                  onClick={handleExecute}
                  disabled={executing || !acknowledged || !capabilities?.execute_available}
                >
                  {executing ? (
                    <Loader2 className='mr-1.5 h-3.5 w-3.5 animate-spin' />
                  ) : (
                    <Play className='mr-1.5 h-3.5 w-3.5' />
                  )}
                  Execute migration
                </Button>
                <Button
                  variant='outline'
                  size='sm'
                  className='h-8'
                  onClick={handlePreflight}
                  disabled={checking}
                >
                  {checking ? (
                    <Loader2 className='mr-1.5 h-3.5 w-3.5 animate-spin' />
                  ) : (
                    <ListChecks className='mr-1.5 h-3.5 w-3.5' />
                  )}
                  Check preflight
                </Button>
                {!capabilities?.execute_available && (
                  <span className='text-xs text-muted-foreground'>
                    Execute is disabled by the backend (gated on{' '}
                    {capabilities?.execute_gate.issue ?? '#7'}).
                  </span>
                )}
              </div>

              {preflightResult && (
                <div className='mt-3 space-y-2'>
                  <div className='flex flex-wrap items-center gap-2 text-sm'>
                    <Badge variant={preflightResult.ok ? 'secondary' : 'outline'}>
                      {preflightResult.ok ? 'Preflight passed' : 'Preflight failed'}
                    </Badge>
                    {preflightResult.storage_checked && (
                      <Badge variant='outline'>
                        storage: {preflightResult.storage_ok ? 'ok' : 'unusable'}
                      </Badge>
                    )}
                  </div>
                  {preflightResult.storage_ok === false &&
                    preflightResult.storage_reason && (
                      <p className='text-xs text-warning-strong'>
                        {preflightResult.storage_reason}
                      </p>
                    )}
                  <div className='min-h-0 overflow-x-auto rounded-lg border border-border'>
                    <table className='w-full text-sm'>
                      <thead className='bg-muted'>
                        <tr className='text-left text-xs text-muted-foreground'>
                          <th className='px-3 py-2 font-medium'>Privilege</th>
                          <th className='px-3 py-2 font-medium'>Needed for</th>
                          <th className='px-3 py-2 font-medium'>Held</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preflightResult.checks.map((c) => (
                          <tr key={c.privilege} className='border-t border-border'>
                            <td className='px-3 py-2 font-medium'>{c.privilege}</td>
                            <td className='px-3 py-2 text-muted-foreground'>{c.reason}</td>
                            <td className='px-3 py-2'>
                              <span
                                className={
                                  c.satisfied
                                    ? 'text-success-strong'
                                    : 'text-destructive'
                                }
                              >
                                {c.satisfied ? 'yes' : 'no'}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>

            {executeResult && (
              <div className='mt-4 min-h-0 overflow-x-auto rounded-lg border border-border'>
                <table className='w-full text-sm'>
                  <thead className='bg-muted'>
                    <tr className='text-left text-xs text-muted-foreground'>
                      <th className='px-3 py-2 font-medium'>#</th>
                      <th className='px-3 py-2 font-medium'>Object</th>
                      <th className='px-3 py-2 font-medium'>Status</th>
                      <th className='px-3 py-2 font-medium'>Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {executeResult.results.map((r) => (
                      <tr key={r.order} className='border-t border-border'>
                        <td className='px-3 py-2 text-muted-foreground'>{r.order}</td>
                        <td className='px-3 py-2 font-medium'>{r.object_name}</td>
                        <td className='px-3 py-2'>
                          <span
                            className={
                              r.status === 'ok'
                                ? 'text-success-strong'
                                : 'text-destructive'
                            }
                          >
                            {r.status}
                          </span>
                        </td>
                        <td className='px-3 py-2 text-muted-foreground'>
                          {r.error ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {executeResult && executeResult.data.length > 0 && (
              <div className='mt-4 space-y-2'>
                <div className='flex flex-wrap gap-2 text-sm'>
                  <Badge variant='secondary'>
                    {executeResult.rows_moved} rows moved
                  </Badge>
                </div>
                <div className='min-h-0 overflow-x-auto rounded-lg border border-border'>
                  <table className='w-full text-sm'>
                    <thead className='bg-muted'>
                      <tr className='text-left text-xs text-muted-foreground'>
                        <th className='px-3 py-2 font-medium'>Table</th>
                        <th className='px-3 py-2 font-medium'>Exported</th>
                        <th className='px-3 py-2 font-medium'>Imported</th>
                        <th className='px-3 py-2 font-medium'>Verified</th>
                        <th className='px-3 py-2 font-medium'>Digest</th>
                        <th className='px-3 py-2 font-medium'>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {executeResult.data.map((c) => (
                        <tr key={c.table} className='border-t border-border'>
                          <td className='px-3 py-2 font-medium'>{c.table}</td>
                          <td className='px-3 py-2 text-muted-foreground'>
                            {c.rows_exported}
                          </td>
                          <td className='px-3 py-2 text-muted-foreground'>
                            {c.rows_imported}
                          </td>
                          <td className='px-3 py-2'>
                            <span
                              className={
                                c.verified
                                  ? 'text-success-strong'
                                  : 'text-destructive'
                              }
                            >
                              {c.verified ? 'yes' : 'no'}
                            </span>
                          </td>
                          <td className='px-3 py-2 text-muted-foreground'>
                            {c.digest_match === null
                              ? '—'
                              : c.digest_match
                                ? 'match'
                                : 'mismatch'}
                          </td>
                          <td className='px-3 py-2 text-muted-foreground'>
                            {c.errors.length > 0
                              ? c.errors.join('; ')
                              : c.note || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  )
}
