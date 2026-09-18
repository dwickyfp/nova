import { createFileRoute } from '@tanstack/react-router';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';
import { Search } from '@/components/search';
import TaskOrchestration from '@/features/task-orchestration';

export const Route = createFileRoute('/_authenticated/task-graphs')({
  component: RouteComponent,
});

function RouteComponent() {
  return (
    <>
      <Header>
        <Search className='me-auto' />
      </Header>
      <Main>
        <TaskOrchestration />
      </Main>
    </>
  );
}
