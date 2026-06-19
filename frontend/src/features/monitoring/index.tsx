import { Outlet } from '@tanstack/react-router'
import { Clock, Zap, Shield, ListTodo, TrendingUp, Upload } from 'lucide-react'
import { Separator } from '@/components/ui/separator'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { SidebarNav } from './components/sidebar-nav'

const sidebarNavItems = [
  {
    title: 'Query History',
    href: '/monitoring',
    icon: <Clock size={18} />,
  },
  {
    title: 'Active Queries',
    href: '/monitoring/active',
    icon: <Zap size={18} />,
  },
  {
    title: 'Audit Trail',
    href: '/monitoring/audit',
    icon: <Shield size={18} />,
  },
  {
    title: 'Tasks',
    href: '/monitoring/tasks',
    icon: <ListTodo size={18} />,
  },
  {
    title: 'Query Cost',
    href: '/monitoring/cost',
    icon: <TrendingUp size={18} />,
  },
  {
    title: 'Data Loads',
    href: '/monitoring/loads',
    icon: <Upload size={18} />,
  },
]

export function MonitoringLayout() {
  return (
    <>
      {/* ===== Top Heading ===== */}
      <Header>
        <Search className='me-auto' />
      </Header>

      <Main fixed>
        <div className='space-y-0.5'>
          <h1 className='text-2xl font-bold tracking-tight md:text-3xl'>
            Monitoring
          </h1>
          <p className='text-muted-foreground'>
            Monitor query performance, audit activity, and track system health.
          </p>
        </div>
        <Separator className='my-4 lg:my-6' />
        <div className='flex flex-1 flex-col space-y-2 overflow-hidden md:space-y-2 lg:flex-row lg:space-y-0 lg:space-x-12'>
          <aside className='top-0 lg:sticky lg:w-1/5'>
            <SidebarNav items={sidebarNavItems} />
          </aside>
          <div className='flex w-full overflow-y-hidden p-1'>
            <Outlet />
          </div>
        </div>
      </Main>
    </>
  )
}
