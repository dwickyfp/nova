1|import { Outlet } from '@tanstack/react-router'
2|import { Monitor, Bell, Palette, Wrench, UserCog } from 'lucide-react'
3|import { Separator } from '@/components/ui/separator'
5|import { Header } from '@/components/layout/header'
6|import { Main } from '@/components/layout/main'
8|import { Search } from '@/components/search'
10|import { SidebarNav } from './components/sidebar-nav'
11|
12|const sidebarNavItems = [
13|  {
14|    title: 'Profile',
15|    href: '/settings',
16|    icon: <UserCog size={18} />,
17|  },
18|  {
19|    title: 'Account',
20|    href: '/settings/account',
21|    icon: <Wrench size={18} />,
22|  },
23|  {
24|    title: 'Appearance',
25|    href: '/settings/appearance',
26|    icon: <Palette size={18} />,
27|  },
28|  {
29|    title: 'Notifications',
30|    href: '/settings/notifications',
31|    icon: <Bell size={18} />,
32|  },
33|  {
34|    title: 'Display',
35|    href: '/settings/display',
36|    icon: <Monitor size={18} />,
37|  },
38|]
39|
40|export function Settings() {
41|  return (
42|    <>
43|      {/* ===== Top Heading ===== */}
44|      <Header>
45|        <Search className='me-auto' />
46|47|48|49|      </Header>
50|
51|      <Main fixed>
52|        <div className='space-y-0.5'>
53|          <h1 className='text-2xl font-bold tracking-tight md:text-3xl'>
54|            Settings
55|          </h1>
56|          <p className='text-muted-foreground'>
57|            Manage your account settings and set e-mail preferences.
58|          </p>
59|        </div>
60|        <Separator className='my-4 lg:my-6' />
61|        <div className='flex flex-1 flex-col space-y-2 overflow-hidden md:space-y-2 lg:flex-row lg:space-y-0 lg:space-x-12'>
62|          <aside className='top-0 lg:sticky lg:w-1/5'>
63|            <SidebarNav items={sidebarNavItems} />
64|          </aside>
65|          <div className='flex w-full overflow-y-hidden p-1'>
66|            <Outlet />
67|          </div>
68|        </div>
69|      </Main>
70|    </>
71|  )
72|}
73|