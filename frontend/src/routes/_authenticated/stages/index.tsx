import { createFileRoute } from '@tanstack/react-router'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { StagesPage } from '@/features/stages'

export const Route = createFileRoute('/_authenticated/stages/')({
  component: () => <>
    <Header fixed />
    <Main fixed className='min-h-0'>
      <StagesPage />
    </Main>
  </>,
})
