import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight, PanelRightClose, Sparkle, SquarePen } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import type { DatavizTurnContext } from "@/features/agents/api";
import { BiFrame } from "@/features/bi/bi-frame";
import { safeBiTarget } from "@/features/bi/bi-target";
import { StudioChat } from "../studio-chat";
import { dockLeft, measureBarGap, supersetBarStacked } from "./superset-bar";
import { DATAVIZ_ASSISTANT } from "./assistant-agent";
import {
  attachDatavizFrame,
  datavizFrameState,
  probeUnsavedChanges,
  syncDatavizFrame,
  useDatavizFrameState,
} from "./dashboard-bridge";

const PANEL_KEY = "nova.dataviz.assistant";
const MIN_WIDTH = 320;
const MAX_WIDTH = 640;
const DEFAULT_WIDTH = 400;

function readPanel(): { open: boolean; width: number } {
  try {
    const stored = JSON.parse(localStorage.getItem(PANEL_KEY) ?? "{}") as {
      open?: boolean;
      width?: number;
    };
    return {
      open: stored.open ?? true,
      width: Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Number(stored.width) || DEFAULT_WIDTH)),
    };
  } catch {
    return { open: true, width: DEFAULT_WIDTH };
  }
}

function writePanel(value: { open: boolean; width: number }) {
  try {
    localStorage.setItem(PANEL_KEY, JSON.stringify(value));
  } catch {
    // A per-browser convenience; the panel still works without it.
  }
}

/** Phones and narrow windows keep Studio's band: Superset stacks its bar there. */
function isNarrow(): boolean {
  return typeof window !== "undefined" && window.innerWidth < 768;
}

/** The dashboard on screen, read fresh when a message is sent. */
function turnContext(): DatavizTurnContext {
  const frame = syncDatavizFrame();
  return {
    dashboard_id: frame.dashboardId,
    native_filters_key: frame.nativeFiltersKey,
    edit_state: probeUnsavedChanges(),
    path: frame.path,
  };
}

/**
 * Studio's Dataviz tab: the whole Superset application, signed in as the
 * current session and active role, with Nova's assistant beside it.
 *
 * The frame is mounted once and stays mounted while the assistant opens,
 * closes or resizes and while the user visits the Agent tab, so an editor
 * with unsaved Superset changes is never reloaded by Nova.
 */
export function DatavizWorkspace({
  path,
  onPathChange,
  onContinueInAgent,
  currentRole,
  displayName,
  visible,
  tabs,
}: {
  path?: string;
  onPathChange: (path: string) => void;
  /** Hand a deeper question to the Agent tab, where the user picks an agent. */
  onContinueInAgent: (prompt: string) => void;
  currentRole: string | null;
  displayName?: string | null;
  /** False while the Agent tab is shown; the frame keeps its state. */
  visible: boolean;
  /** Studio's workspace switch, docked in Superset's bar when there is room. */
  tabs?: React.ReactNode;
}) {
  const isMobile = useIsMobile();
  const [panel, setPanel] = useState(readPanel);
  const [source, setSource] = useState(() => safeBiTarget(path));
  const [threadId, setThreadId] = useState<string | null>(null);
  const [newChatNonce, setNewChatNonce] = useState(0);
  const frame = useDatavizFrameState();
  const reported = useRef(path);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const clusterRef = useRef<HTMLDivElement | null>(null);
  // Docked in Superset's own bar (no header row), or in Studio's band. A wide
  // screen starts docked; the first measurement confirms or moves it.
  const [dock, setDock] = useState<{ left: number; top: number } | null>(null);
  const [docked, setDocked] = useState(() => !isNarrow());

  const placeControls = useCallback(() => {
    const root = rootRef.current;
    const node = frameRef.current;
    const cluster = clusterRef.current;
    if (!root || !node || !cluster) return;
    if (isNarrow()) {
      setDocked(false);
      return;
    }
    const gap = measureBarGap(node);
    if (!gap) {
      // Superset is still loading or has no bar here: keep the last placement
      // unless its bar is there and stacked, which never leaves room.
      if (supersetBarStacked(node)) setDocked(false);
      return;
    }
    const left = dockLeft(gap, cluster.offsetWidth);
    if (left === null) {
      setDocked(false);
      return;
    }
    const offset = node.getBoundingClientRect().left - root.getBoundingClientRect().left;
    const next = {
      left: Math.round(offset + left),
      top: Math.round((gap.height - cluster.offsetHeight) / 2),
    };
    setDocked(true);
    setDock((current) =>
      current && current.left === next.left && current.top === next.top ? current : next,
    );
  }, []);

  useEffect(() => writePanel(panel), [panel]);
  useEffect(() => () => attachDatavizFrame(null), []);

  // Superset routes client-side without load events; follow the frame while shown.
  useEffect(() => {
    if (!visible) return;
    const timer = window.setInterval(() => {
      syncDatavizFrame();
      placeControls();
    }, 1000);
    const root = rootRef.current;
    const observer = new ResizeObserver(() => placeControls());
    if (root) observer.observe(root);
    placeControls();
    return () => {
      window.clearInterval(timer);
      observer.disconnect();
    };
  }, [visible, placeControls]);

  useEffect(() => {
    if (!frame.path || frame.path === reported.current) return;
    reported.current = frame.path;
    onPathChange(frame.path);
  }, [frame.path, onPathChange]);

  // A link (for example "Open in Dataviz") asks for another page.
  useEffect(() => {
    const wanted = safeBiTarget(path);
    if (!path || wanted === reported.current || wanted === datavizFrameState().path) return;
    if (probeUnsavedChanges() === "dirty") {
      toast.error("Save or discard your Dataviz changes first; nothing was reloaded.");
      return;
    }
    reported.current = wanted;
    setSource(wanted);
  }, [path]);

  const setFrame = useCallback((node: HTMLIFrameElement | null) => {
    frameRef.current = node;
    if (node) attachDatavizFrame(node);
  }, []);

  // Opening, closing or resizing the panel moves Superset's bar.
  useEffect(() => placeControls(), [panel.open, panel.width, placeControls]);

  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = panel.width;
    const move = (moveEvent: PointerEvent) => {
      const width = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + startX - moveEvent.clientX));
      setPanel((current) => ({ ...current, width }));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const panelOpen = panel.open;
  return (
    // With room in Superset's bar, the switch and the Assistant button dock in
    // its empty middle and there is no header row: Superset's bar and the
    // assistant's header form the one top bar. Without room (narrow frames,
    // a stacked bar) they sit in Studio's own band above both columns, which
    // still share one top rule. Superset is never covered.
    <div
      ref={rootRef}
      className={cn("relative flex h-full min-h-0 w-full", !(docked && dock) && "pt-14")}
    >
      <div
        className={cn(
          "pointer-events-none absolute z-20",
          docked && dock ? "" : "inset-x-0 top-3 flex justify-center px-4",
        )}
        style={docked && dock ? { left: dock.left, top: dock.top } : undefined}
      >
        <div ref={clusterRef} className="pointer-events-auto flex items-center gap-2">
          {tabs}
          {!panelOpen ? (
            <div className="nova-mode-switch rounded-full p-1">
              <button
                type="button"
                aria-expanded={false}
                aria-controls="dataviz-assistant"
                onClick={() => setPanel((current) => ({ ...current, open: true }))}
                className="flex h-8 items-center gap-2 rounded-full px-3 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Sparkle aria-hidden="true" className="size-4" strokeWidth={1.5} />
                Assistant
              </button>
            </div>
          ) : null}
        </div>
      </div>
      <section aria-label="Dataviz" className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div
          className={cn(
            "flex min-h-0 flex-1 flex-col overflow-hidden bg-background",
            !(docked && dock) && "border-t",
          )}
        >
          <BiFrame ref={setFrame} path={source} title="Dataviz" onLoad={() => syncDatavizFrame()} holdsEdits />
        </div>
      </section>

      {isMobile && panelOpen ? (
        <button
          type="button"
          aria-label="Close assistant"
          className="absolute inset-0 z-30 bg-background/70"
          onClick={() => setPanel((current) => ({ ...current, open: false }))}
        />
      ) : null}
      <aside
        id="dataviz-assistant"
        aria-label="Nova assistant"
        hidden={!panelOpen}
        style={isMobile ? undefined : { width: panel.width }}
        className={cn(
          "flex min-h-0 flex-col border-l bg-background",
          !(docked && dock) && "border-t",
          isMobile ? "absolute inset-y-0 right-0 z-40 w-[min(100%,28rem)] shadow-lg" : "relative shrink-0",
        )}
      >
        {!isMobile ? (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize assistant"
            aria-valuemin={MIN_WIDTH}
            aria-valuemax={MAX_WIDTH}
            aria-valuenow={panel.width}
            tabIndex={0}
            onPointerDown={startResize}
            onKeyDown={(event) => {
              const step = event.key === "ArrowLeft" ? 24 : event.key === "ArrowRight" ? -24 : 0;
              if (!step) return;
              event.preventDefault();
              setPanel((current) => ({
                ...current,
                width: Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, current.width + step)),
              }));
            }}
            className="absolute inset-y-0 -left-1 z-10 w-2 cursor-col-resize focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        ) : null}
        {/* As tall as Superset's own header (61px with its rule), so both rules meet. */}
        <header className="flex h-[61px] shrink-0 items-center gap-3 border-b pr-2 pl-4">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-medium text-foreground">Assistant</h2>
            <p className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
              <span
                aria-hidden="true"
                className={cn(
                  "size-1.5 shrink-0 rounded-full",
                  frame.dashboardId ? "bg-success" : "bg-muted-foreground/40",
                )}
              />
              <span className="truncate">
                {frame.dashboardId
                  ? `Reading ${frame.title ?? `dashboard ${frame.dashboardId}`}`
                  : "Open a dashboard to ask about it"}
              </span>
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-0.5">
            <HeaderAction
              label="New chat"
              onClick={() => {
                setThreadId(null);
                setNewChatNonce((value) => value + 1);
              }}
            >
              <SquarePen aria-hidden="true" className="size-4" strokeWidth={1.5} />
            </HeaderAction>
            <HeaderAction
              label="Continue in Agent"
              onClick={() => {
                const open = datavizFrameState();
                onContinueInAgent(
                  open.dashboardId
                    ? `Lanjutkan analisis dashboard Dataviz #${open.dashboardId}: `
                    : "",
                );
              }}
            >
              <ArrowUpRight aria-hidden="true" className="size-4" strokeWidth={1.5} />
            </HeaderAction>
            <HeaderAction
              label="Close assistant"
              onClick={() => setPanel((current) => ({ ...current, open: false }))}
            >
              <PanelRightClose aria-hidden="true" className="size-4" strokeWidth={1.5} />
            </HeaderAction>
          </div>
        </header>
        <div className="flex min-h-0 flex-1 flex-col">
          <StudioChat
            variant="panel"
            agent={DATAVIZ_ASSISTANT}
            agents={[DATAVIZ_ASSISTANT]}
            onSelectAgent={() => undefined}
            currentRole={currentRole}
            displayName={displayName}
            activeThreadId={threadId}
            onThreadChange={setThreadId}
            newChatNonce={newChatNonce}
            datavizContext={turnContext}
          />
        </div>
      </aside>
    </div>
  );
}

/** A quiet icon action in the assistant's header, named by its tooltip. */
function HeaderAction({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={label}
          className="size-8 rounded-lg text-muted-foreground hover:text-foreground"
          onClick={onClick}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}
