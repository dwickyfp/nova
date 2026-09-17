import { createFileRoute } from '@tanstack/react-router'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import FunctionsPage from '@/features/functions'

export const Route = createFileRoute('/_authenticated/functions/')({
  component: RouteComponent,
})

function RouteComponent() {
  return (
    <>
      <Header>
        <Search className='me-auto' />
      </Header>
      <Main fixed>
        <FunctionsPage />
      </Main>
    </>
  )
}
