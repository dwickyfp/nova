import { useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { agentsApi } from "@/features/agents/api";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { RuleProposalPanel } from "./rule-proposal-panel";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

export function AgentMemoryDialog({ agentId }: { agentId: string }) {
  const [open, setOpen] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [ruleMemoryId, setRuleMemoryId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const memories = useInfiniteQuery({
    queryKey: ["agents", agentId, "memories"],
    queryFn: ({ pageParam }) => agentsApi.listMemories(agentId, pageParam),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset ?? undefined,
    enabled: open,
  });
  const items = memories.data?.pages.flatMap((page) => page.memories) ?? [];
  const remove = useMutation({
    mutationFn: (memoryId: string) => agentsApi.deleteMemory(agentId, memoryId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agents", agentId, "memories"] });
      toast.success("Memory deleted");
    },
    onError: () => toast.error("Memory could not be deleted"),
  });

  return (
    <>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          aria-label="View agent memory"
          className="size-11 shrink-0 gap-1.5 px-2 text-xs text-muted-foreground sm:size-auto sm:h-8"
        >
          <BookOpen aria-hidden="true" className="size-3.5" />
          <span className="hidden sm:inline">Memory</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Agent memory</DialogTitle>
          <DialogDescription>
            Facts this agent remembers from your conversations under your current role. Each account and agent has its own memories.
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 overflow-y-auto pe-3">
          {memories.isLoading ? (
            <p className="py-6 text-sm text-muted-foreground">Loading memories…</p>
          ) : memories.isError && !items.length ? (
            <div className="py-6 text-sm" role="alert">
              <p>Memories could not be loaded.</p>
              <Button variant="outline" size="sm" className="mt-3" onClick={() => void memories.refetch()}>
                Try again
              </Button>
            </div>
          ) : items.length ? (
            <>
            <ul className="divide-y">
              {items.map((memory) => (
                <li key={memory.memory_id} className="py-4 first:pt-0">
                  <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="whitespace-pre-wrap break-words text-sm text-foreground">{memory.fact}</p>
                    <p className="mt-1 break-words text-xs text-muted-foreground">
                      From your message: “{memory.source_quote}”
                    </p>
                    <Button
                      variant="link"
                      size="sm"
                      className="mt-1 h-auto px-0 text-xs"
                      aria-expanded={ruleMemoryId === memory.memory_id}
                      onClick={() => setRuleMemoryId(
                        ruleMemoryId === memory.memory_id ? null : memory.memory_id,
                      )}
                    >
                      {ruleMemoryId === memory.memory_id ? "Close rule proposal" : "Propose as business rule"}
                    </Button>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-11 shrink-0 text-muted-foreground hover:text-destructive sm:size-8"
                    aria-label={`Delete memory: ${memory.fact}`}
                    title="Delete memory"
                    disabled={remove.isPending}
                    onClick={() => setPendingDeleteId(memory.memory_id)}
                  >
                    <Trash2 aria-hidden="true" className="size-4" />
                  </Button>
                  </div>
                  {ruleMemoryId === memory.memory_id ? (
                    <div className="mt-3"><RuleProposalPanel agentId={agentId} memory={memory} /></div>
                  ) : null}
                </li>
              ))}
            </ul>
            {memories.hasNextPage ? (
              <Button
                variant="outline"
                className="mt-3 w-full"
                disabled={memories.isFetchingNextPage}
                onClick={() => void memories.fetchNextPage()}
              >
                {memories.isFetchingNextPage ? "Loading…" : "Load more memories"}
              </Button>
            ) : null}
            {memories.isFetchNextPageError ? (
              <p role="alert" className="mt-2 text-sm text-destructive">
                More memories could not be loaded. Try again.
              </p>
            ) : null}
            </>
          ) : (
            <p className="py-6 text-sm text-muted-foreground">
              No memories yet. Explain a lasting business rule during a conversation and this agent can remember it.
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
    <ConfirmDialog
      open={pendingDeleteId !== null}
      onOpenChange={(nextOpen) => { if (!nextOpen) setPendingDeleteId(null); }}
      title="Delete memory?"
      desc="This saved memory will be removed from the agent."
      confirmText="Delete memory"
      destructive
      isLoading={remove.isPending}
      handleConfirm={() => {
        const memoryId = pendingDeleteId;
        setPendingDeleteId(null);
        if (memoryId) remove.mutate(memoryId);
      }}
    />
    </>
  );
}
