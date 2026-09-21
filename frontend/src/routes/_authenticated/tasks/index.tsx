import { createFileRoute } from "@tanstack/react-router";
import { Header } from "@/components/layout/header";
import { Main } from "@/components/layout/main";
import { Search } from "@/components/search";
import { MonitoringPageScroller } from "@/features/monitoring";
import { TaskList } from "@/features/task-orchestration";

export const Route = createFileRoute("/_authenticated/tasks/")({
  component: RouteComponent,
});

function RouteComponent() {
  return (
    <>
      <Header>
        <Search className="me-auto" />
      </Header>
      <Main fixed className="min-h-0">
        <MonitoringPageScroller>
          <div className="flex min-h-full flex-col">
            <TaskList />
          </div>
        </MonitoringPageScroller>
      </Main>
    </>
  );
}
