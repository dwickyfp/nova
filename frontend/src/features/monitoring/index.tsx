import { Outlet } from '@tanstack/react-router'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'

export function MonitoringLayout() {
  return (
    <>
      <Header>
        <Search className='me-auto' />
      </Header>
      <Main fixed>
        <Outlet />
      </Main>
    </>
  )
}
