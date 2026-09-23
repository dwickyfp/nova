import type { ReactNode } from 'react'
import { Outlet } from '@tanstack/react-router'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'

export function MonitoringPageScroller({ children }: { children: ReactNode }) {
  return (
    <div className='minimal-scrollbar min-h-0 w-full flex-1 overflow-y-auto'>
      <div className='px-4 py-6 @7xl/content:mx-auto @7xl/content:w-full @7xl/content:max-w-7xl'>
        {children}
      </div>
    </div>
  )
}

export function MonitoringLayout() {
  return (
    <>
      <Header>
        <Search className='me-auto' />
      </Header>
      <Main fixed fluid className='min-h-0 p-0'>
        <MonitoringPageScroller>
          <Outlet />
        </MonitoringPageScroller>
      </Main>
    </>
  )
}
