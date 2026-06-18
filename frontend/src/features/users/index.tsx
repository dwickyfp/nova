1|import { getRouteApi } from '@tanstack/react-router'
3|import { Header } from '@/components/layout/header'
4|import { Main } from '@/components/layout/main'
6|import { Search } from '@/components/search'
8|import { UsersDialogs } from './components/users-dialogs'
9|import { UsersPrimaryButtons } from './components/users-primary-buttons'
10|import { UsersProvider } from './components/users-provider'
11|import { UsersTable } from './components/users-table'
12|import { users } from './data/users'
13|
14|const route = getRouteApi('/_authenticated/users/')
15|
16|export function Users() {
17|  const search = route.useSearch()
18|  const navigate = route.useNavigate()
19|
20|  return (
21|    <UsersProvider>
22|      <Header fixed>
23|        <Search className='me-auto' />
24|25|26|27|      </Header>
28|
29|      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
30|        <div className='flex flex-wrap items-end justify-between gap-2'>
31|          <div>
32|            <h2 className='text-2xl font-bold tracking-tight'>User List</h2>
33|            <p className='text-muted-foreground'>
34|              Manage your users and their roles here.
35|            </p>
36|          </div>
37|          <UsersPrimaryButtons />
38|        </div>
39|        <UsersTable data={users} search={search} navigate={navigate} />
40|      </Main>
41|
42|      <UsersDialogs />
43|    </UsersProvider>
44|  )
45|}
46|