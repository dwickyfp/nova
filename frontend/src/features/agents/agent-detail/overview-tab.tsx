import { useQuery } from '@tanstack/react-query'
import { Activity, Coins, MessageSquare, Users } from 'lucide-react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Skeleton } from '@/components/ui/skeleton'
import { observabilityApi } from '@/features/agents/api'

/**
 * Agent Overview — usage over the last 7 days.
 *
 * Three headline stats (sessions, tokens, active users) and a daily session
 * chart. All figures come from the assistant message store, where each run
 * records its token usage; nothing is estimated.
 */
export function AgentOverviewTab({ agentId }: { agentId: string }) {
  const usageQuery = useQuery({
    queryKey: ['agents', 'observability', 'usage', agentId],
    queryFn: () => observabilityApi.usage(agentId, 7),
  })

  if (usageQuery.isLoading) {
    return (
      <div className='space-y-4'>
        <div className='grid gap-3 sm:grid-cols-3'>
          <Skeleton className='h-24 w-full' />
          <Skeleton className='h-24 w-full' />
          <Skeleton className='h-24 w-full' />
        </div>
        <Skeleton className='h-64 w-full' />
      </div>
    )
  }

  const usage = usageQuery.data
  const series = usage?.series ?? []

  return (
    <div className='space-y-6'>
      <h2 className='text-lg font-medium'>Usage over the last 7 days</h2>

      <div className='grid gap-3 sm:grid-cols-3'>
        <StatCard
          icon={MessageSquare}
          label='Total sessions'
          value={usage?.total_sessions ?? 0}
        />
        <StatCard
          icon={Coins}
          label='Total tokens'
          value={(usage?.total_tokens ?? 0).toLocaleString()}
        />
        <StatCard
          icon={Users}
          label='Total active users'
          value={usage?.total_active_users ?? 0}
        />
      </div>

      <div className='rounded-2xl border p-4'>
        <div className='mb-4 flex items-center gap-2 text-sm text-muted-foreground'>
          <Activity className='size-4' />
          Sessions per day
        </div>
        <div className='h-64 w-full'>
          <ResponsiveContainer width='100%' height='100%'>
            <LineChart data={series} margin={{ top: 4, right: 12, bottom: 0, left: -20 }}>
              <CartesianGrid strokeDasharray='3 3' stroke='var(--border)' vertical={false} />
              <XAxis
                dataKey='date'
                tickFormatter={(value: string) => value.slice(5)}
                tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                allowDecimals={false}
                tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                contentStyle={{
                  background: 'var(--popover)',
                  border: '1px solid var(--border)',
                  borderRadius: 12,
                  fontSize: 12,
                }}
              />
              <Line
                type='monotone'
                dataKey='sessions'
                stroke='var(--primary)'
                strokeWidth={2}
                dot={{ r: 2 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof MessageSquare
  label: string
  value: string | number
}) {
  return (
    <div className='rounded-2xl border p-4'>
      <div className='flex items-center gap-2 text-sm text-muted-foreground'>
        <Icon className='size-4' />
        {label}
      </div>
      <div className='mt-2 text-3xl font-semibold tracking-tight'>{value}</div>
    </div>
  )
}
