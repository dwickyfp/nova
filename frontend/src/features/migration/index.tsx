import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Ban, Loader2, Play, RefreshCw, Search } from 'lucide-react'
import { toast } from 'sonner'
import { Header } from '@/components/layout/header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  dryRun,
  enumerateObjects,
  fetchCapabilities,
  fetchEngineStatus,
  fetchSources,
  createSource,
  type DryRunResponse,
  type EngineStatus,
  type MigrationCapabilities,
  type MigrationVerdict,
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
  const [storageConnection, setStorageConnection] = useState('production')
  const [submitting, setSubmitting] = useState(false)

  const [database, setDatabase] = useState('')
  const [objects, setObjects] = useState<SourceObject[]>([])
  const [enumerating, setEnumerating] = useState(false)
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null)
  const [running, setRunning] = useState(false)

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
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to load migration status')
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleAddSource = async () => {
    if (!sourceName.trim()) {
      toast.error('Source name is required')
      return
    }
    setSubmitting(true)
    try {
      await createSource({
        name: sourceName.trim(),
        storage_connection: storageConnection.trim() || 'production',
      })
      toast.success(`Source "${sourceName}" registered`)
      setSourceName('')
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to register source')
    } finally {
      setSubmitting(false)
    }
  }

  const handleEnumerate = async () => {
    if (!database.trim()) {
      toast.error('Database name is required')
      return
    }
    setEnumerating(true)
    setDryRunResult(null)
    try {
      const res = await enumerateObjects(database.trim())
      setObjects(res.objects)
      toast.success(`${res.count} objects found in ${res.database}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to enumerate objects')
    } finally {
      setEnumerating(false)
    }
  }

  const handleDryRun = async () => {
    if (!database.trim()) {
      toast.error('Database name is required')
      return
    }
    setRunning(true)
    try {
      const res = await dryRun(database.trim())
      setDryRunResult(res)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Dry-run failed')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className='flex h-full min-h-0 flex-col'>
      <Header fixed>
        <div className='flex min-w-0 flex-1 items-center gap-3'>
          <div className='min-w-0'>
            <h1 className='truncate text-lg font-semibold'>Migration Connector</h1>
            <p className='text-sm text-muted-foreground'>
              Assess a source StarRocks cluster and preview a dry-run. Cutover is
              gated and not available here.
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
            Execute is not part of this release
          </div>
          <p className='mt-1 text-muted-foreground'>
            v1 only assesses and dry-runs. No cutover runs behind any flag;
            execution is gated on{' '}
            {capabilities?.execute_gate.issue ?? '#7'}{' '}
            ({capabilities?.execute_gate.name ?? 'backup/restore'}).
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
          <h2 className='text-sm font-semibold'>2. Source connections</h2>
          <div className='mt-3 flex flex-wrap items-end gap-2'>
            <div className='space-y-1.5'>
              <Label htmlFor='source-name'>Name</Label>
              <Input
                id='source-name'
                value={sourceName}
                onChange={(e) => setSourceName(e.target.value)}
                placeholder='prod-source'
                className='h-8 w-48'
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='source-storage'>Storage connection</Label>
              <Input
                id='source-storage'
                value={storageConnection}
                onChange={(e) => setStorageConnection(e.target.value)}
                placeholder='production'
                className='h-8 w-48'
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
                    → {source.storage_connection}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className='rounded-lg border border-border bg-background p-4'>
          <h2 className='text-sm font-semibold'>3. Enumerate &amp; dry-run</h2>
          <div className='mt-3 flex items-end gap-2'>
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
      </div>
    </div>
  )
}
