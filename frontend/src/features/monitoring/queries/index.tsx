import { lazy, Suspense } from 'react'
import { LoadingLines } from '@/components/ui/loading-overlay'
import { PageHeader } from '@/components/ui/page-header'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

const MonitoringActiveQueries = lazy(() =>
  import('../active-queries').then((m) => ({ default: m.MonitoringActiveQueries })),
)
const MonitoringQueryHistory = lazy(() =>
  import('../query-history').then((m) => ({ default: m.MonitoringQueryHistory })),
)
const MonitoringQueryCost = lazy(() =>
  import('../query-cost').then((m) => ({ default: m.MonitoringQueryCost })),
)
const QueryAutopilot = lazy(() =>
  import('../autopilot').then((m) => ({ default: m.QueryAutopilot })),
)

export const QUERY_TABS = ['running', 'history', 'cost', 'autopilot'] as const
export type QueryTab = (typeof QUERY_TABS)[number]

const TABS: { value: QueryTab; label: string }[] = [
  { value: 'running', label: 'Running now' },
  { value: 'history', label: 'History' },
  { value: 'cost', label: 'Cost' },
  { value: 'autopilot', label: 'Autopilot' },
]

/**
 * Every view of SQL queries in one place: what runs now, what ran, what it
 * cost, and what Autopilot suggests. Only the open tab mounts and fetches.
 */
export function MonitoringQueries({
  tab,
  onTabChange,
}: {
  tab: QueryTab
  onTabChange: (tab: QueryTab) => void
}) {
  return (
    <div className='min-w-0 space-y-5'>
      <PageHeader
        title='Queries'
        description='What runs now, what ran, what it cost, and how Autopilot can make it faster.'
      />
      <Tabs value={tab} onValueChange={(value) => onTabChange(value as QueryTab)} className='gap-5'>
        <TabsList className='h-auto max-w-full shrink-0 justify-start self-start overflow-x-auto [scrollbar-width:none] [&>[role=tab]]:flex-none'>
          {TABS.map((item) => (
            <TabsTrigger key={item.value} value={item.value}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <Suspense fallback={<LoadingLines rows={6} />}>
          <TabsContent value='running' className='mt-0 min-w-0'>
            <MonitoringActiveQueries embedded />
          </TabsContent>
          <TabsContent value='history' className='mt-0 min-w-0'>
            <MonitoringQueryHistory embedded />
          </TabsContent>
          <TabsContent value='cost' className='mt-0 min-w-0'>
            <MonitoringQueryCost embedded />
          </TabsContent>
          <TabsContent value='autopilot' className='mt-0 min-w-0'>
            <QueryAutopilot embedded />
          </TabsContent>
        </Suspense>
      </Tabs>
    </div>
  )
}
