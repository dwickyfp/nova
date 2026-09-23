import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileText,
  MessageSquare,
  Plug,
  Plus,
  Search,
  Upload,
} from "lucide-react";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/auth-store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { skillsApi, studioApi, type Skill } from "@/features/agents/api";
import { SkillEditor } from "./skill-editor";
import { skillDocument } from "./skill-document";
import { SkillUpload } from "./skill-upload";

export function StudioCapabilities({
  onCreateWithChat,
  creatingChat = false,
}: {
  onCreateWithChat: () => void;
  creatingChat?: boolean;
}) {
  const [tab, setTab] = useState("skills");
  const [search, setSearch] = useState("");
  const [editor, setEditor] = useState<{
    document: string;
    id?: string;
  } | null>(null);
  const [deleting, setDeleting] = useState<Skill | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const queryClient = useQueryClient();
  const owner = useAuthStore((state) => state.auth.user?.username);
  const skills = useQuery({
    queryKey: ["studio", "skills", owner],
    queryFn: skillsApi.personal,
  });
  const caps = useQuery({
    queryKey: ["studio", "capabilities", owner],
    queryFn: studioApi.capabilities,
    enabled: tab === "connectors",
  });
  const remove = useMutation({
    mutationFn: (id: string) => skillsApi.remove(id),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["studio", "skills"] }),
        queryClient.invalidateQueries({ queryKey: ["studio", "capabilities"] }),
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
      ]);
      setDeleting(null);
      toast.success("Skill deleted");
    },
  });
  const query = search.trim().toLowerCase();
  const visibleSkills = (skills.data?.skills ?? []).filter(
    (skill) =>
      skill.source !== "builtin" &&
      `${skill.name} ${skill.description}`.toLowerCase().includes(query),
  );
  const connectors = (caps.data?.connectors ?? []).filter((connector) =>
    `${connector.name} ${connector.description}`.toLowerCase().includes(query),
  );

  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <aside className="shrink-0 border-b bg-sidebar px-3 py-5 md:w-60 md:border-r md:border-b-0 md:py-8">
          <h1 className="px-3 text-lg font-heading">
            Capabilities
          </h1>
          <nav
            aria-label="Capability categories"
            className="mt-5 flex gap-1 md:mt-7 md:flex-col"
          >
            {[
              { id: "skills", label: "Skills" },
              { id: "connectors", label: "MCP Connectors" },
            ].map((item) => (
              <Button
                key={item.id}
                variant="ghost"
                aria-current={tab === item.id ? "page" : undefined}
                className={cn(
                  "h-auto justify-start px-3 py-3 md:w-full",
                  tab === item.id && "bg-accent text-accent-foreground",
                )}
                onClick={() => {
                  setTab(item.id);
                  setSearch("");
                }}
              >
                {item.label}
              </Button>
            ))}
          </nav>
        </aside>
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-5 py-6 md:px-10 md:py-8 xl:px-20">
          <div
            className={cn(
              "flex flex-wrap items-start justify-between gap-4 pb-8",
              tab === "skills" && "border-b",
            )}
          >
            <div>
              <h2
                id="capability-heading"
                className="text-lg font-heading"
              >
                {tab === "skills" ? "Your skills" : "MCP Connectors"}
              </h2>
              {tab === "connectors" ? (
                <p className="mt-1 text-sm text-muted-foreground">
                  Internal MCP connectors managed by your administrator.
                </p>
              ) : null}
            </div>
            <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
              <div className="relative min-w-0 flex-1 sm:w-40">
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
                />
                <Input
                  aria-label={
                    tab === "skills" ? "Search skills" : "Search connectors"
                  }
                  placeholder="Search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  className="border-transparent bg-muted pl-9 shadow-none"
                />
              </div>
              {tab === "skills" ? (
                <SkillCreateMenu
                  onChat={onCreateWithChat}
                  onUpload={() => setUploadOpen(true)}
                  creatingChat={creatingChat}
                >
                  <Button variant="secondary" className="shadow-none">
                    <Plus aria-hidden="true" className="size-4" />
                    Create
                  </Button>
                </SkillCreateMenu>
              ) : null}
            </div>
          </div>
          {tab === "skills" ? (
            <section aria-labelledby="capability-heading">
              {skills.isPending ? (
                <p
                  role="status"
                  className="py-10 text-sm text-muted-foreground"
                >
                  Loading your skills…
                </p>
              ) : skills.isError ? (
                <LoadError
                  label="Your skills could not be loaded."
                  retry={() => void skills.refetch()}
                />
              ) : visibleSkills.length ? (
                <ul className="divide-y">
                  {visibleSkills.map((skill) => (
                    <li
                      key={skill.skill_id}
                      className="flex flex-wrap items-center gap-3 py-5"
                    >
                      <FileText
                        aria-hidden="true"
                        className="size-5 shrink-0 text-muted-foreground"
                      />
                      <button
                        className="min-w-0 flex-1 rounded-sm text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                        onClick={() =>
                          setEditor({
                            document: skillDocument(skill),
                            id: skill.skill_id,
                          })
                        }
                      >
                        <span className="block break-words text-sm font-medium">
                          {skill.name}
                        </span>
                        <span className="mt-1 block break-words text-sm leading-relaxed text-muted-foreground">
                          {skill.description}
                        </span>
                      </button>
                      <Button
                        variant="ghost"
                        className="min-h-11 text-muted-foreground"
                        aria-label={`Delete ${skill.name}`}
                        onClick={() => {
                          remove.reset();
                          setDeleting(skill);
                        }}
                      >
                        Delete
                      </Button>
                    </li>
                  ))}
                </ul>
              ) : query ? (
                <p className="py-12 text-sm text-muted-foreground">
                  No skills match your search.
                </p>
              ) : (
                <div className="mx-auto w-full max-w-sm py-12 md:pt-24">
                  <h3 className="text-lg font-medium">
                    Get started with skills
                  </h3>
                  <SkillCreateMenu
                    onChat={onCreateWithChat}
                    onUpload={() => setUploadOpen(true)}
                    creatingChat={creatingChat}
                  >
                    <Button
                      variant="outline"
                      className="mt-4 h-auto w-full flex-col items-start gap-3 whitespace-normal p-4 text-left shadow-none"
                    >
                      <span className="flex items-center gap-2">
                        <FileText aria-hidden="true" className="size-4" />
                        Create a skill
                      </span>
                      <span className="text-sm font-normal leading-relaxed text-muted-foreground">
                        Turn everyday tasks into workflows Nova can repeat.
                      </span>
                    </Button>
                  </SkillCreateMenu>
                </div>
              )}
            </section>
          ) : (
            <section aria-labelledby="capability-heading">
              {caps.isPending ? (
                <p
                  role="status"
                  className="py-10 text-sm text-muted-foreground"
                >
                  Loading connectors…
                </p>
              ) : caps.isError ? (
                <LoadError
                  label="Connectors could not be loaded."
                  retry={() => void caps.refetch()}
                />
              ) : connectors.length ? (
                <ul className="divide-y">
                  {connectors.map((connector) => (
                    <li
                      key={connector.server_id}
                      className="flex items-start gap-4 py-5"
                    >
                      <Plug
                        aria-hidden="true"
                        className="mt-1 size-5 shrink-0 text-muted-foreground"
                      />
                      <div className="min-w-0 flex-1">
                        <h3 className="break-words text-sm font-medium">
                          {connector.name}
                        </h3>
                        <p className="mt-1 break-words text-sm text-muted-foreground">
                          {connector.description}
                        </p>
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {connector.is_active ? "Listed" : "Disabled"}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="py-12">
                  <h3 className="text-sm font-medium">
                    {query
                      ? "No connectors match your search"
                      : "No internal connectors yet"}
                  </h3>
                  <p className="mt-2 text-sm text-muted-foreground">
                    {query
                      ? "Try another name."
                      : "Connectors will appear here when your administrator adds them to the internal catalog."}
                  </p>
                </div>
              )}
            </section>
          )}
        </div>
      </div>
      {uploadOpen ? <SkillUpload onClose={() => setUploadOpen(false)} /> : null}
      {editor ? (
        <SkillEditor
          initialDocument={editor.document}
          skillId={editor.id}
          onClose={() => setEditor(null)}
        />
      ) : null}
      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setDeleting(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete skill?</DialogTitle>
            <DialogDescription>
              Delete {deleting?.name} from your skills. Your agents will no
              longer be able to load it.
            </DialogDescription>
          </DialogHeader>
          {remove.error ? (
            <p role="alert" className="text-sm text-destructive">
              {remove.error.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleting(null)}
              disabled={remove.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => deleting && remove.mutate(deleting.skill_id)}
            >
              {remove.isPending ? "Deleting…" : "Delete skill"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function LoadError({ label, retry }: { label: string; retry: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 py-10">
      <p className="text-sm">{label}</p>
      <Button variant="outline" onClick={retry}>
        Retry
      </Button>
    </div>
  );
}

function SkillCreateMenu({
  children,
  onChat,
  onUpload,
  creatingChat,
}: {
  children: React.ReactNode;
  onChat: () => void;
  onUpload: () => void;
  creatingChat: boolean;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={onChat} disabled={creatingChat}>
          <MessageSquare aria-hidden="true" />
          {creatingChat ? "Opening chat…" : "Create with chat"}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onUpload}>
          <Upload aria-hidden="true" />
          Upload a skill
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
