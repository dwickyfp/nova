2|import { Header } from '@/components/layout/header'
3|import { Main } from '@/components/layout/main'
5|import { Search } from '@/components/search'
7|import { TasksDialogs } from './components/tasks-dialogs'
8|import { TasksPrimaryButtons } from './components/tasks-primary-buttons'
9|import { TasksProvider } from './components/tasks-provider'
10|import { TasksTable } from './components/tasks-table'
11|import { tasks } from './data/tasks'
12|
13|export function Tasks() {
14|  return (
15|    <TasksProvider>
16|      <Header fixed>
17|        <Search className='me-auto' />
18|19|20|21|      </Header>
22|
23|      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
24|        <div className='flex flex-wrap items-end justify-between gap-2'>
25|          <div>
26|            <h2 className='text-2xl font-bold tracking-tight'>Tasks</h2>
27|            <p className='text-muted-foreground'>
28|              Here&apos;s a list of your tasks for this month!
29|            </p>
30|          </div>
31|          <TasksPrimaryButtons />
32|        </div>
33|        <TasksTable data={tasks} />
34|      </Main>
35|
36|      <TasksDialogs />
37|    </TasksProvider>
38|  )
39|}
40|