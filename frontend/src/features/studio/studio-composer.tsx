import { useId, useMemo, useRef, useState } from "react";
import { ArrowUp, ChevronDown, FileText, ImageIcon, Plus, Square, Telescope, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverAnchor } from "@/components/ui/popover";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import type { Agent } from "@/features/agents/api";
import { SKILL_AUTHOR_ID } from "./skill-document";
import { StudioCommandMenu } from "./studio-command-menu";
import { commandOptionId, commandQuery, matchCommands, type StudioCommand } from "./studio-commands";
import { ACCEPTED_EXTENSIONS, MAX_FILES, type PendingAttachment } from "./studio-attachments";

function AgentPicker({
  agent,
  agents,
  onSelectAgent,
  disabled,
}: {
  agent: Agent | null;
  agents: Agent[];
  onSelectAgent: (id: string) => void;
  disabled: boolean;
}) {
  if (agent?.agent_id === SKILL_AUTHOR_ID) {
    return <span className="text-xs text-muted-foreground">Skill creator</span>;
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className="flex h-8 min-w-0 items-center gap-1 rounded-full border border-border bg-card px-2.5 text-xs text-card-foreground shadow-xs transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <span className="truncate">{agent?.name ?? "Select an agent"}</span>
          <ChevronDown aria-hidden="true" className="size-3 shrink-0" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56 bg-card text-card-foreground">
        <DropdownMenuLabel>Agents</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {agents.length === 0 ? (
          <DropdownMenuItem disabled>No agents available</DropdownMenuItem>
        ) : (
          agents.map((a) => (
            <DropdownMenuItem key={a.agent_id} onSelect={() => onSelectAgent(a.agent_id)}>
              {a.name}
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}


export const Composer = ({
  ref,
  value,
  onChange,
  onKeyDown,
  onSend,
  onResearch,
  onStop,
  readingFiles,
  attachments,
  onAddFiles,
  onRemoveAttachment,
  streaming,
  disabled,
  agent,
  agents,
  onSelectAgent,
  sendDisabled = false,
  agentPicker = true,
}: {
  sendDisabled?: boolean;
  /** False where Nova picks the agent itself (the Dataviz assistant). */
  agentPicker?: boolean;
  ref: React.RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (v: string) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  /** Present when the agent can run Deep research on the typed question. */
  onResearch?: () => void;
  onStop: () => void;
  readingFiles: boolean;
  attachments: PendingAttachment[];
  onAddFiles: (files: FileList) => void;
  onRemoveAttachment: (id: string) => void;
  streaming: boolean;
  disabled: boolean;
  agent: Agent | null;
  agents: Agent[];
  onSelectAgent: (id: string) => void;
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const [focused, setFocused] = useState(false);
  const [dismissedAt, setDismissedAt] = useState<string | null>(null);
  const [activeState, setActiveState] = useState({ query: "", index: 0 });
  const query = commandQuery(value);
  const matches = useMemo(() => (query === null ? [] : matchCommands(query)), [query]);
  const menuOpen = focused && !disabled && matches.length > 0 && dismissedAt !== value;
  const active =
    activeState.query === query ? Math.min(activeState.index, matches.length - 1) : 0;
  const setActive = (index: number) => setActiveState({ query: query ?? "", index });

  const choose = (command: StudioCommand) => {
    onChange(`${command.name} `);
    ref.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (menuOpen && !e.nativeEvent.isComposing) {
      const count = matches.length;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setActive((active + (e.key === "ArrowDown" ? 1 : count - 1)) % count);
        return;
      }
      if ((e.key === "Enter" && !e.shiftKey) || (e.key === "Tab" && !e.shiftKey)) {
        e.preventDefault();
        choose(matches[active]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setDismissedAt(value);
        return;
      }
    }
    onKeyDown(e);
  };

  return (
    <div className="mx-auto w-full max-w-3xl">
      <Popover open={menuOpen} onOpenChange={(open) => !open && setDismissedAt(value)}>
      <PopoverAnchor asChild>
      <div ref={boxRef} className="flex w-full flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm transition-colors focus-within:border-ring">
        <input
          ref={fileInputRef}
          type="file"
          aria-label="Choose files"
          className="sr-only"
          tabIndex={-1}
          accept={ACCEPTED_EXTENSIONS.join(",")}
          multiple
          onChange={(event) => {
            if (event.currentTarget.files?.length) onAddFiles(event.currentTarget.files);
            event.currentTarget.value = "";
          }}
        />
        {attachments.length ? (
          <div className="flex flex-wrap gap-1.5 px-3 pt-3" aria-label="Files ready to send">
            {attachments.map((item) => (
              <span key={item.id} className="inline-flex max-w-full items-center gap-1 rounded-lg border bg-muted px-2 py-1 text-xs">
                {item.mediaType.startsWith("image/") ? (
                  <ImageIcon aria-hidden="true" className="size-3.5 shrink-0" />
                ) : (
                  <FileText aria-hidden="true" className="size-3.5 shrink-0" />
                )}
                <span className="max-w-40 truncate" title={item.name}>{item.name}</span>
                <button type="button" aria-label={`Remove ${item.name}`} disabled={streaming} onClick={() => onRemoveAttachment(item.id)} className="rounded p-0.5 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40">
                  <X aria-hidden="true" className="size-3.5" />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          aria-label="Message Nova"
          aria-autocomplete="list"
          aria-controls={menuOpen ? listId : undefined}
          aria-activedescendant={menuOpen ? commandOptionId(listId, active) : undefined}
          placeholder="Ask a question about your data"
          rows={1}
          disabled={disabled}
          className="min-h-14 max-h-48 w-full shrink-0 resize-none bg-transparent px-4 pt-4 pb-2 text-sm leading-relaxed outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
        />
        <div className="flex items-center gap-1 px-2.5 pb-2.5">
          <Button
            variant="ghost"
            size="icon"
            className="size-8 rounded-full border border-border bg-card text-muted-foreground hover:bg-accent"
            disabled={disabled || streaming || readingFiles || attachments.length >= MAX_FILES}
            aria-label="Add file"
            title="Add text, PDF, or image (up to 3 files)"
            onClick={() => fileInputRef.current?.click()}
          >
            <Plus aria-hidden="true" className="size-4" strokeWidth={1} />
          </Button>

          {agentPicker ? (
            <AgentPicker agent={agent} agents={agents}
              disabled={streaming || readingFiles}
              onSelectAgent={onSelectAgent} />
          ) : null}

          <div className="flex-1" />
          {onResearch ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-8 rounded-full text-muted-foreground"
              disabled={disabled || value.trim().length < 3}
              title="Break the question into several investigations and write a report. Takes a few minutes."
              onClick={onResearch}
            >
              <Telescope aria-hidden="true" className="size-4" />
              <span className="hidden sm:inline">Deep research</span>
            </Button>
          ) : null}
          <button
            type="button"
            onClick={streaming ? onStop : onSend}
            disabled={disabled || sendDisabled || readingFiles || (!streaming && !value.trim() && !attachments.length)}
            aria-label={streaming ? "Stop" : "Send"}
            className={cn(
              "flex size-9 items-center justify-center rounded-full transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
              streaming
                ? "bg-secondary text-foreground hover:bg-accent"
                : "bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-40",
            )}
          >
            {streaming ? (
              <Square aria-hidden="true" className="size-3.5 fill-current" />
            ) : (
              <ArrowUp aria-hidden="true" className="size-4" />
            )}
          </button>
        </div>
      </div>
      </PopoverAnchor>
      {menuOpen ? (
        <StudioCommandMenu
          listId={listId}
          commands={matches}
          active={active}
          onActiveChange={setActive}
          onChoose={choose}
          anchorRef={boxRef}
        />
      ) : null}
      </Popover>
      <p className="sr-only" aria-live="polite">
        {menuOpen
          ? `${matches.length} ${matches.length === 1 ? "command" : "commands"} available. Use the up and down arrows to choose, Enter to insert.`
          : ""}
      </p>
      <p className="mt-2 text-center text-xs text-muted-foreground">
        Enter to send, Shift+Enter for a new line. Type / for commands.
      </p>
    </div>
  );
};

Composer.displayName = "Composer";
