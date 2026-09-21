import { useQuery } from "@tanstack/react-query";
import { BookOpen, Bot, LayoutGrid, Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { studioApi } from "@/features/agents/api";

/** Capabilities — the agents, skills, and tools available to this user. */
export function StudioCapabilities({
  onSelectAgent,
}: {
  onSelectAgent: (id: string) => void;
}) {
  const capsQuery = useQuery({
    queryKey: ["studio", "capabilities"],
    queryFn: () => studioApi.capabilities(),
  });

  return (
    <>
      <header className="flex h-14 shrink-0 items-center border-b px-6">
        <h1 className="font-medium">Capabilities</h1>
      </header>
      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto w-full max-w-3xl space-y-8 px-6 py-10">
          {capsQuery.isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <>
              <Section
                icon={Bot}
                title="Agents"
                count={capsQuery.data?.agents.length ?? 0}
              >
                <div className="grid gap-2 sm:grid-cols-2">
                  {capsQuery.data?.agents.map((agent) => (
                    <button
                      key={agent.agent_id}
                      type="button"
                      onClick={() => onSelectAgent(agent.agent_id)}
                      className="rounded-lg border p-3 text-left transition-colors hover:bg-accent/50"
                    >
                      <span className="text-sm font-medium">{agent.name}</span>
                      {agent.description ? (
                        <span className="mt-1 block line-clamp-2 text-xs text-muted-foreground">
                          {agent.description}
                        </span>
                      ) : null}
                    </button>
                  ))}
                </div>
              </Section>

              <Section
                icon={Wrench}
                title="Tools"
                count={capsQuery.data?.tools.length ?? 0}
              >
                <div className="flex flex-wrap gap-2">
                  {capsQuery.data?.tools.map((tool) => (
                    <Badge
                      key={`${tool.source}-${tool.name}`}
                      variant={tool.is_enabled ? "secondary" : "outline"}
                      className="font-mono"
                    >
                      {tool.name}
                    </Badge>
                  ))}
                </div>
              </Section>

              <Section
                icon={BookOpen}
                title="Your skills"
                count={capsQuery.data?.skills.length ?? 0}
              >
                {capsQuery.data?.skills.length ? (
                  <div className="flex flex-wrap gap-2">
                    {capsQuery.data.skills.map((skill) => (
                      <Badge
                        key={skill.name}
                        variant="secondary"
                        className="font-mono"
                      >
                        {skill.name}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    No custom skills yet. Add one under AI &amp; ML &gt; Skills.
                  </p>
                )}
              </Section>

              <div className="flex justify-center">
                <Button
                  variant="outline"
                  onClick={() =>
                    onSelectAgent(capsQuery.data?.agents[0]?.agent_id ?? "")
                  }
                >
                  <LayoutGrid className="size-4" />
                  Open an agent
                </Button>
              </div>
            </>
          )}
        </div>
      </ScrollArea>
    </>
  );
}

function Section({
  icon: Icon,
  title,
  count,
  children,
}: {
  icon: typeof Bot;
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-3 flex items-center gap-2">
        <Icon className="size-4" />
        <h2 className="text-sm font-medium">{title}</h2>
        <Badge variant="outline">{count}</Badge>
      </div>
      {children}
    </section>
  );
}
