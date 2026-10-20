import { createFileRoute } from '@tanstack/react-router'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { MonitoringPageScroller } from '@/features/monitoring'
import { MonitoringQueryCost } from '@/features/monitoring/query-cost'

export const Route = createFileRoute('/_authenticated/query-cost')({
  component: RouteComponent,
})

function RouteComponent() {
  return (
    <>
      <Header>
        <Search className='me-auto' />
      </Header>
      <Main fixed className='min-h-0'>
        <MonitoringPageScroller>
          <MonitoringQueryCost />
        </MonitoringPageScroller>
      </Main>
    </>
  )
}
