import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Gauge,
  RefreshCw,
  Server,
  Timer,
  Users,
} from 'lucide-react'
import { useEffect } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import {
  LoadingLines,
  LoadingOverlay,
  RefreshBanner,
} from '@/components/ui/loading-overlay'
import { MetricCard } from '@/components/ui/metric-card'
import { PageHeader } from '@/components/ui/page-header'
import { StatusBadge } from '@/components/ui/status-badge'
import { cn } from '@/lib/utils'
import {
  countAlive,
  fetchFeMetrics,
  fetchNodes,
  hasAnyMetric,
  metricValue,
  successRate,
  type NodeRow,
} from './api'

function formatNumber(value: number | null | undefined) {
  if (value == null) return '-'
  return value.toLocaleString()
}

function formatLatency(value: number | null | undefined) {
  if (value == null) return '-'
  if (value < 1000) return `${value.toFixed(0)} ms`
  return `${(value / 1000).toFixed(2)} s`
}

function nodeTone(node: NodeRow) {
  if (node.alive === true) return 'success' as const
  if (node.alive === false) return 'danger' as const
  return 'neutral' as const
}

function nodeStatusLabel(node: NodeRow) {
  if (node.alive === true) return 'Alive'
  if (node.alive === false) return 'Down'
  return 'Unknown'
}

function NodeTable({
  title,
  nodes,
  isLoading,
  isError,
  onRetry,
}: {
  title: string
  nodes: NodeRow[]
  isLoading: boolean
  isError: boolean
  onRetry: () => void
}) {
  const { alive, total } = countAlive(nodes)

  return (
    <section className='rounded-xl border border-border bg-surface-2'>
      <div className='flex flex-wrap items-center justify-between gap-2 border-b border-border px-5 py-3'>
        <div className='flex items-center gap-2'>
          <Server className='size-4 text-muted-foreground' />
          <h3 className='text-sm font-medium'>{title}</h3>
        </div>
        {!isLoading && !isError && total > 0 ? (
          <StatusBadge tone={alive === total ? 'success' : 'warning'}>
            {alive}/{total} alive
          </StatusBadge>
        ) : null}
      </div>

      {isLoading ? (
        <div className='px-5 py-4'>
          <LoadingLines rows={3} />
        </div>
      ) : isError ? (
        <div className='px-5 py-4'>
          <EmptyState
            variant='error'
            icon={AlertCircle}
            title={`Could not load ${title.toLowerCase()}`}
            description='The engine did not answer the node inventory query. The cluster may still be serving queries.'
            action={
              <Button variant='outline' size='sm' onClick={onRetry}>
                Retry
              </Button>
            }
          />
        </div>
      ) : nodes.length === 0 ? (
        <div className='px-5 py-4 text-sm text-muted-foreground'>
          No nodes reported.
        </div>
      ) : (
        <div className='overflow-x-auto'>
          <table className='w-full'>
            <thead>
              <tr className='border-b border-border'>
                <th className='px-5 py-2.5 text-left text-xs font-medium text-muted-foreground'>
                  Host
                </th>
                <th className='px-5 py-2.5 text-left text-xs font-medium text-muted-foreground'>
                  Role
                </th>
                <th className='px-5 py-2.5 text-left text-xs font-medium text-muted-foreground'>
                  Port
                </th>
                <th className='px-5 py-2.5 text-left text-xs font-medium text-muted-foreground'>
                  Last heartbeat
                </th>
                <th className='px-5 py-2.5 text-right text-xs font-medium text-muted-foreground'>
                  State
                </th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((node, index) => (
                <tr
                  key={`${node.host}-${node.port}-${index}`}
                  className='border-b border-border last:border-0'
                >
                  <td className='px-5 py-2.5 font-mono text-xs'>{node.host || '-'}</td>
                  <td className='px-5 py-2.5'>
                    {node.role ? (
                      <Badge variant='secondary'>{node.role}</Badge>
                    ) : (
                      <span className='text-xs text-muted-foreground'>-</span>
                    )}
                  </td>
                  <td className='px-5 py-2.5 font-mono text-xs text-muted-foreground'>
                    {node.port || '-'}
                  </td>
                  <td className='px-5 py-2.5 text-xs text-muted-foreground'>
                    {node.lastHeartbeat
                      ? new Date(node.lastHeartbeat).toLocaleString(undefined, {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })
                      : '-'}
                  </td>
                  <td className='px-5 py-2.5 text-right'>
                    <StatusBadge tone={nodeTone(node)} dot>
                      {nodeStatusLabel(node)}
                    </StatusBadge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function ClusterMonitorPage() {
  const frontendsQuery = useQuery({
    queryKey: ['cluster', 'frontends'],
    queryFn: () => fetchNodes('frontends'),
  })
  const backendsQuery = useQuery({
    queryKey: ['cluster', 'backends'],
    queryFn: () => fetchNodes('backends'),
  })
  const metricsQuery = useQuery({
    queryKey: ['cluster', 'fe-metrics'],
    queryFn: fetchFeMetrics,
  })

  useEffect(() => {
    if (metricsQuery.error) {
      toast.error('Failed to load FE metrics', {
        description: metricsQuery.error.message,
      })
    }
  }, [metricsQuery.error])

  const metrics = metricsQuery.data?.metrics
  const metricsAvailable = hasAnyMetric(metrics)
  const rate = successRate(metrics)
  const frontends = frontendsQuery.data ?? []
  const backends = backendsQuery.data ?? []

  const isRefreshing =
    frontendsQuery.isFetching || backendsQuery.isFetching || metricsQuery.isFetching

  return (
    <div className='space-y-6'>
      <PageHeader
        title='Cluster Monitor'
        description='Frontend and backend node inventory plus frontend health counters, read live from the engine.'
        actions={
          <Button
            variant='outline'
            size='sm'
            disabled={isRefreshing}
            onClick={() => {
              void frontendsQuery.refetch()
              void backendsQuery.refetch()
              void metricsQuery.refetch()
            }}
          >
            <RefreshCw className={cn('me-1.5 size-4', isRefreshing && 'animate-spin')} />
            Refresh
          </Button>
        }
      />

      <section>
        <h2 className='mb-3 text-sm font-medium text-muted-foreground'>
          Frontend health
        </h2>
        {metricsQuery.isLoading ? (
          <LoadingOverlay label='Reading frontend metrics...' />
        ) : metricsQuery.isError ? (
          <EmptyState
            variant='error'
            icon={AlertCircle}
            title='Could not load frontend metrics'
            description='The monitoring endpoint did not respond. Node inventory below is unaffected.'
            action={
              <Button variant='outline' size='sm' onClick={() => void metricsQuery.refetch()}>
                Retry
              </Button>
            }
          />
        ) : !metricsAvailable ? (
          <EmptyState
            icon={Gauge}
            title='No frontend counters reported'
            description='The engine returned an empty metric set. This is normal on a cluster whose FE has not served a query yet.'
          />
        ) : (
          <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
            <MetricCard
              weight='primary'
              label='Success rate'
              value={rate == null ? '-' : `${(rate * 100).toFixed(1)}%`}
              icon={CheckCircle2}
              tone={rate != null && rate >= 0.99 ? 'success' : 'warning'}
              hint={`${formatNumber(metricValue(metrics, 'query_success'))} of ${formatNumber(metricValue(metrics, 'query_total'))} queries`}
            />
            <MetricCard
              label='Failed queries'
              value={formatNumber(metricValue(metrics, 'query_err'))}
              icon={AlertTriangle}
              tone='danger'
              hint='Returns an error, or is killed'
            />
            <MetricCard
              label='Slow queries'
              value={formatNumber(metricValue(metrics, 'slow_query'))}
              icon={Timer}
              tone='warning'
              hint='Above the engine slow-query threshold'
            />
            <MetricCard
              label='Connections'
              value={formatNumber(metricValue(metrics, 'connection_total'))}
              icon={Users}
              tone='info'
            />
            <MetricCard
              weight='compact'
              label='Total queries'
              value={formatNumber(metricValue(metrics, 'query_total'))}
              icon={Activity}
            />
            <MetricCard
              weight='compact'
              label='Avg latency'
              value={formatLatency(metricValue(metrics, 'query_latency_ms'))}
              icon={Gauge}
            />
            <MetricCard
              weight='compact'
              label='p95 latency'
              value={formatLatency(metricValue(metrics, 'query_latency_95th_ms'))}
              icon={Gauge}
            />
            <MetricCard
              weight='compact'
              label='p99 latency'
              value={formatLatency(metricValue(metrics, 'query_latency_99th_ms'))}
              icon={Gauge}
            />
          </div>
        )}
      </section>

      <div className='relative space-y-5'>
        {isRefreshing &&
        !frontendsQuery.isLoading &&
        !backendsQuery.isLoading ? (
          <RefreshBanner label='Refreshing cluster state...' />
        ) : null}
        <NodeTable
          title='Frontends'
          nodes={frontends}
          isLoading={frontendsQuery.isLoading}
          isError={frontendsQuery.isError}
          onRetry={() => void frontendsQuery.refetch()}
        />
        <NodeTable
          title='Backends'
          nodes={backends}
          isLoading={backendsQuery.isLoading}
          isError={backendsQuery.isError}
          onRetry={() => void backendsQuery.refetch()}
        />
      </div>
    </div>
  )
}
