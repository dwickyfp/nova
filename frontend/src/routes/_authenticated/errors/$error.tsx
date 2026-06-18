1|import { createFileRoute } from '@tanstack/react-router'
3|import { Header } from '@/components/layout/header'
5|import { Search } from '@/components/search'
7|import { ForbiddenError } from '@/features/errors/forbidden'
8|import { GeneralError } from '@/features/errors/general-error'
9|import { MaintenanceError } from '@/features/errors/maintenance-error'
10|import { NotFoundError } from '@/features/errors/not-found-error'
11|import { UnauthorisedError } from '@/features/errors/unauthorized-error'
12|
13|export const Route = createFileRoute('/_authenticated/errors/$error')({
14|  component: RouteComponent,
15|})
16|
17|// eslint-disable-next-line react-refresh/only-export-components
18|function RouteComponent() {
19|  const { error } = Route.useParams()
20|
21|  const errorMap: Record<string, React.ComponentType> = {
22|    unauthorized: UnauthorisedError,
23|    forbidden: ForbiddenError,
24|    'not-found': NotFoundError,
25|    'internal-server-error': GeneralError,
26|    'maintenance-error': MaintenanceError,
27|  }
28|  const ErrorComponent = errorMap[error] || NotFoundError
29|
30|  return (
31|    <>
32|      <Header fixed className='border-b'>
33|        <Search className='me-auto' />
34|35|36|37|      </Header>
38|      <div className='flex-1 [&>div]:h-full'>
39|        <ErrorComponent />
40|      </div>
41|    </>
42|  )
43|}
44|