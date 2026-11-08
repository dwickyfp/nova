import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, MessageSquareText, PanelRightClose, RotateCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import type { DatavizTurnContext } from "@/features/agents/api";
import { BiFrame } from "@/features/bi/bi-frame";
import { safeBiTarget } from "@/features/bi/bi-target";
import { StudioChat } from "../studio-chat";
import { DATAVIZ_ASSISTANT } from "./assistant-agent";
import {
  attachDatavizFrame,
  datavizFrameState,
  probeUnsavedChanges,
  reloadDatavizFrameIfClean,
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
}: {
  path?: string;
  onPathChange: (path: string) => void;
  /** Hand a deeper question to the Agent tab, where the user picks an agent. */
  onContinueInAgent: (prompt: string) => void;
  currentRole: string | null;
  displayName?: string | null;
  /** False while the Agent tab is shown; the frame keeps its state. */
  visible: boolean;
}) {
  const isMobile = useIsMobile();
  const [panel, setPanel] = useState(readPanel);
  const [source, setSource] = useState(() => safeBiTarget(path));
  const [threadId, setThreadId] = useState<string | null>(null);
  const [newChatNonce, setNewChatNonce] = useState(0);
  const frame = useDatavizFrameState();
  const reported = useRef(path);

  useEffect(() => writePanel(panel), [panel]);
  useEffect(() => () => attachDatavizFrame(null), []);

  // Superset routes client-side without load events; follow the frame while shown.
  useEffect(() => {
    if (!visible) return;
    const timer = window.setInterval(() => syncDatavizFrame(), 1000);
    return () => window.clearInterval(timer);
  }, [visible]);

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
    if (node) attachDatavizFrame(node);
  }, []);

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
    <div className="relative flex h-full min-h-0 w-full">
      <section aria-label="Dataviz" className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center justify-end gap-1 px-3 pb-2 md:pb-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label="Reload Dataviz"
                onClick={() => {
                  if (!reloadDatavizFrameIfClean())
                    toast.error("Save or discard your Dataviz changes before reloading.");
                }}
              >
                <RotateCw aria-hidden="true" className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Reload</TooltipContent>
          </Tooltip>
          <Button variant="ghost" size="sm" className="h-8" asChild>
            <a
              href={`/bi-launch?next=${encodeURIComponent(frame.path ?? source)}`}
              target="_blank"
              rel="noopener"
            >
              <ExternalLink aria-hidden="true" className="mr-1.5 size-4" />
              Open in new tab
            </a>
          </Button>
          <Button
            type="button"
            variant={panelOpen ? "secondary" : "outline"}
            size="sm"
            className="h-8"
            aria-expanded={panelOpen}
            aria-controls="dataviz-assistant"
            onClick={() => setPanel((current) => ({ ...current, open: !current.open }))}
          >
            <MessageSquareText aria-hidden="true" className="mr-1.5 size-4" />
            Assistant
          </Button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-tl-lg border-t border-l bg-background">
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
        <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2">
          <p className="min-w-0 flex-1 truncate text-sm font-medium">Nova assistant</p>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => {
              const open = datavizFrameState();
              onContinueInAgent(
                open.dashboardId
                  ? `Lanjutkan analisis dashboard Dataviz #${open.dashboardId}: `
                  : "",
              );
            }}
          >
            Continue in Agent
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => {
              setThreadId(null);
              setNewChatNonce((value) => value + 1);
            }}
          >
            New chat
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="Close assistant"
            onClick={() => setPanel((current) => ({ ...current, open: false }))}
          >
            <PanelRightClose aria-hidden="true" className="size-4" />
          </Button>
        </div>
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
