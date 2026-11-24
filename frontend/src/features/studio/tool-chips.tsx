import { memo, useId, useMemo, useState, type CSSProperties } from "react";
import {
  BarChart3,
  BookOpen,
  ChevronDown,
  Database,
  ExternalLink,
  Globe,
  Plug,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { CitationBlock } from "@/features/assistant/types";
import { cn } from "@/lib/utils";
import { CitationList } from "./result-cards";
import { REVEAL_STEP_EVENT } from "./rail-model";
import type { RailStep } from "./thought-turn";
import {
  formatDuration,
  toolChips,
  type ToolChip,
  type ToolChipCall,
  type ToolKind,
} from "./tool-chip-model";

const KIND_ICON: Record<ToolKind, LucideIcon> = {
  mcp: Plug,
  skill: BookOpen,
  data: Database,
  chart: BarChart3,
  web: Globe,
  other: Wrench,
};

/** The accent of each kind, from the theme's tool tokens. */
const KIND_ACCENT: Record<ToolKind, string> = {
  mcp: "var(--tool-mcp)",
  skill: "var(--tool-skill)",
  data: "var(--tool-data)",
  chart: "var(--tool-chart)",
  web: "var(--tool-web)",
  other: "var(--tool-other)",
};

const STATUS_TEXT = { success: "Succeeded", error: "Failed", cancelled: "Not finished" } as const;

const STATUS_DOT = {
  success: "bg-success-strong",
  error: "bg-destructive",
  cancelled: "bg-muted-foreground",
} as const;

function revealStep(stepId: string) {
  window.dispatchEvent(new CustomEvent(REVEAL_STEP_EVENT, { detail: { stepId } }));
}

/** Open the folded table a call returned, and bring it into view. */
function revealData(stepId: string) {
  const target = document.querySelector<HTMLElement>(`[data-tool-call-id="${CSS.escape(stepId)}"]`);
  if (!target) return;
  const folded = target.closest("details");
  if (folded) folded.open = true;
  target.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

/**
 * The tools an answer used, one chip per distinct tool. A chip opens a card
 * with each call: its input, result, duration and status, linked to its step.
 */
export const ToolChips = memo(function ToolChips({
  steps,
  citations,
  dataCalls,
}: {
  steps: RailStep[];
  citations?: CitationBlock[];
  /** Calls whose result is shown as a table in the answer. */
  dataCalls?: ReadonlySet<string>;
}) {
  const { chips, sources } = useMemo(() => toolChips(steps, citations), [steps, citations]);
  const [open, setOpen] = useState<string | null>(null);
  const baseId = useId();
  if (!chips.length && !sources.length) return null;
  const openChip = chips.find((chip) => chip.key === open);
  const detailId = `${baseId}-detail`;
  return (
    <section aria-label="Tools used" className="nova-chat-item flex min-w-0 flex-col gap-2" data-testid="tool-chips">
      {chips.length ? (
        <ul className="flex flex-wrap gap-2" role="list">
          {chips.map((chip) => (
            <li key={chip.key} className="min-w-0 max-w-full">
              <Chip
                chip={chip}
                expanded={open === chip.key}
                controls={detailId}
                onToggle={() => setOpen((current) => (current === chip.key ? null : chip.key))}
              />
            </li>
          ))}
        </ul>
      ) : null}
      {openChip ? (
        <Detail id={detailId} chip={openChip} dataCalls={dataCalls} />
      ) : null}
      {sources.length ? <CitationList citations={sources} /> : null}
    </section>
  );
});

function Chip({
  chip,
  expanded,
  controls,
  onToggle,
}: {
  chip: ToolChip;
  expanded: boolean;
  controls: string;
  onToggle: () => void;
}) {
  const Icon = KIND_ICON[chip.kind];
  const count = chip.calls.length;
  return (
    <button
      type="button"
      aria-expanded={expanded}
      aria-controls={expanded ? controls : undefined}
      aria-label={`${chip.name}, ${chip.source}${count > 1 ? `, ${count} calls` : ""}, ${STATUS_TEXT[chip.status].toLowerCase()}`}
      onClick={onToggle}
      data-kind={chip.kind}
      style={{ "--accent-tool": KIND_ACCENT[chip.kind] } as CSSProperties}
      className={cn(
        "group flex h-9 max-w-full min-w-0 items-center gap-2 rounded-lg border bg-card py-1 pr-2 pl-1 text-left text-sm",
        "transition-[transform,box-shadow,border-color] duration-150 ease-out motion-reduce:transition-none",
        "hover:-translate-y-px hover:border-[color-mix(in_oklab,var(--accent-tool)_45%,var(--border))] hover:shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent-tool)_14%,transparent)] motion-reduce:hover:translate-y-0",
        "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
        expanded && "border-[color-mix(in_oklab,var(--accent-tool)_55%,var(--border))] shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent-tool)_14%,transparent)]",
      )}
    >
      <span
        aria-hidden="true"
        className="flex size-7 shrink-0 items-center justify-center rounded-md bg-[color-mix(in_oklab,var(--accent-tool)_14%,transparent)] text-[var(--accent-tool)]"
      >
        <Icon className="size-3.5" />
      </span>
      <span className="min-w-0 truncate font-medium text-foreground">{chip.name}</span>
      <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">{chip.source}</span>
      {count > 1 ? (
        <span className="shrink-0 rounded-sm bg-muted px-1 font-mono text-[11px] text-muted-foreground tabular-nums">
          ×{count}
        </span>
      ) : null}
      <span aria-hidden="true" className={cn("size-1.5 shrink-0 rounded-full", STATUS_DOT[chip.status])} />
      <ChevronDown
        aria-hidden="true"
        className={cn(
          "size-3.5 shrink-0 text-muted-foreground transition-transform duration-150 motion-reduce:transition-none",
          expanded && "rotate-180",
        )}
      />
    </button>
  );
}

function Detail({
  id,
  chip,
  dataCalls,
}: {
  id: string;
  chip: ToolChip;
  dataCalls?: ReadonlySet<string>;
}) {
  return (
    <div
      id={id}
      role="region"
      aria-label={`${chip.name} calls`}
      style={{ "--accent-tool": KIND_ACCENT[chip.kind] } as CSSProperties}
      className="min-w-0 rounded-lg border border-l-2 border-l-[var(--accent-tool)] bg-card text-sm"
    >
      <ol className="divide-y">
        {chip.calls.map((call, index) => (
          <CallRow
            key={`${call.stepId}-${index}`}
            call={call}
            index={chip.calls.length > 1 ? index + 1 : undefined}
            hasData={Boolean(dataCalls?.has(call.stepId))}
          />
        ))}
      </ol>
      {chip.links.length ? (
        <ul className="flex flex-col gap-1 border-t px-3 py-2" aria-label="Pages found">
          {chip.links.map((link) => (
            <li key={link.url} className="min-w-0">
              <a
                href={link.url}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="inline-flex max-w-full items-center gap-1 text-xs underline-offset-2 hover:underline"
              >
                <span className="truncate">{link.title}</span>
                <ExternalLink aria-hidden="true" className="size-3 shrink-0" />
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function CallRow({ call, index, hasData }: { call: ToolChipCall; index?: number; hasData: boolean }) {
  const duration = formatDuration(call.durationMs);
  return (
    <li className="flex min-w-0 flex-col gap-1.5 px-3 py-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {index ? <span className="font-medium text-foreground">Call {index}</span> : null}
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className={cn("size-1.5 rounded-full", STATUS_DOT[call.status])} />
          {STATUS_TEXT[call.status]}
        </span>
        {duration ? <span className="font-mono tabular-nums">{duration}</span> : null}
        <span className="ml-auto flex gap-3">
          {hasData ? (
            <button
              type="button"
              className="rounded-sm underline-offset-2 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              onClick={() => revealData(call.stepId)}
            >
              Show data
            </button>
          ) : null}
          <button
            type="button"
            className="rounded-sm underline-offset-2 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            onClick={() => revealStep(call.stepId)}
          >
            Show in steps
          </button>
        </span>
      </div>
      {call.input ? (
        <pre
          className={cn(
            "max-h-40 overflow-auto rounded-md bg-muted/60 px-2 py-1.5 text-xs whitespace-pre-wrap break-words",
            call.sql ? "font-mono" : "font-sans",
          )}
        >
          {call.input}
        </pre>
      ) : null}
      {call.reason ? <p className="text-xs text-destructive break-words">{call.reason}</p> : null}
      {call.result ? <p className="line-clamp-4 text-xs text-muted-foreground">{call.result}</p> : null}
    </li>
  );
}
