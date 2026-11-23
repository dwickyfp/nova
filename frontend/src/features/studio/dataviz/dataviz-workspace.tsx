import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight, PanelRightClose, Sparkle, SquarePen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import type { DatavizTurnContext } from "@/features/agents/api";
import type { NativeDashboardContext } from "@/features/native-dataviz/dashboard/editor";
import { NativeDataviz } from "@/features/native-dataviz/native-dataviz";
import { StudioChat } from "../studio-chat";
import { DATAVIZ_ASSISTANT } from "./assistant-agent";

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

/**
 * Studio's Dataviz tab: native dashboards, with the Dataviz assistant beside them.
 *
 * The workspace stays mounted while the Agent tab is shown, so an open
 * dashboard and its viewer state survive switching tabs.
 */
export function DatavizWorkspace({
  onContinueInAgent,
  currentRole,
  displayName,
  tabs,
}: {
  /** Hand a deeper question to the Agent tab, where the user picks an agent. */
  onContinueInAgent: (prompt: string) => void;
  currentRole: string | null;
  displayName?: string | null;
  /** False while the Agent tab is shown; the workspace keeps its state. */
  visible?: boolean;
  /** Studio's Agent/Dataviz switch. */
  tabs?: React.ReactNode;
}) {
  const isMobile = useIsMobile();
  const [panel, setPanel] = useState(readPanel);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [newChatNonce, setNewChatNonce] = useState(0);
  const [resizing, setResizing] = useState(false);
  const [askPrompt, setAskPrompt] = useState("");
  const nativeContext = useRef<NativeDashboardContext | null>(null);
  const [dashboardOpen, setDashboardOpen] = useState(false);

  // On a phone the panel covers the dashboard, so it starts closed there and
  // the remembered open/closed choice belongs to wider screens.
  const phoneClosed = useRef(false);
  useEffect(() => {
    if (!isMobile || phoneClosed.current) return;
    phoneClosed.current = true;
    setPanel((current) => ({ ...current, open: false }));
  }, [isMobile]);
  useEffect(() => {
    if (!isMobile) writePanel(panel);
  }, [panel, isMobile]);

  const onNativeContext = useCallback((context: NativeDashboardContext | null) => {
    nativeContext.current = context;
    setDashboardOpen(Boolean(context));
  }, []);
  // Read fresh when a message is sent, so the assistant always edits what is on screen.
  const turnContext = useCallback(
    (): DatavizTurnContext => ({
      dashboard_id: null,
      native_filters_key: null,
      edit_state: "unknown",
      path: null,
      native_dashboard: nativeContext.current,
    }),
    [],
  );
  const askNeva = useCallback((prompt: string) => {
    setPanel((current) => ({ ...current, open: true }));
    // Clear first so asking the same question twice fills the composer again.
    setAskPrompt("");
    window.setTimeout(() => setAskPrompt(prompt), 0);
  }, []);

  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    setResizing(true);
    const startX = event.clientX;
    const startWidth = panel.width;
    const move = (moveEvent: PointerEvent) => {
      const width = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + startX - moveEvent.clientX));
      setPanel((current) => ({ ...current, width }));
    };
    const up = () => {
      setResizing(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const panelOpen = panel.open;
  return (
    <div className="relative flex h-full min-h-0 w-full flex-col">
      <div className="flex h-14 shrink-0 items-center justify-center gap-2 px-4 max-sm:justify-start max-sm:overflow-x-auto max-sm:px-2">
        {tabs}
        {!panelOpen ? (
          <div className="nova-mode-switch rounded-full p-1">
            <button
              type="button"
              aria-expanded={false}
              aria-controls="dataviz-assistant"
              onClick={() => setPanel((current) => ({ ...current, open: true }))}
              className="flex h-8 items-center gap-2 rounded-full px-3 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              <Sparkle aria-hidden="true" className="size-4" strokeWidth={1.5} />
              <span className="max-sm:sr-only">Assistant</span>
            </button>
          </div>
        ) : null}
      </div>
      <div className="flex min-h-0 flex-1 border-t">
        <section aria-label="Dataviz" className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background">
          <NativeDataviz onContextChange={onNativeContext} onAskNeva={askNeva} />
        </section>

        {isMobile ? (
          <button
            type="button"
            aria-label="Close assistant"
            tabIndex={panelOpen ? 0 : -1}
            aria-hidden={!panelOpen}
            className={cn(
              "absolute inset-0 z-30 bg-background/70 transition-opacity duration-200 ease-in-out motion-reduce:transition-none",
              panelOpen ? "opacity-100" : "pointer-events-none opacity-0",
            )}
            onClick={() => setPanel((current) => ({ ...current, open: false }))}
          />
        ) : null}
        {/* The panel stays mounted and slides, so the conversation survives
            closing it; `inert` keeps a closed panel out of the tab order. */}
        <div
          data-state={panelOpen ? "open" : "closed"}
          style={isMobile ? undefined : { width: panelOpen ? panel.width : 0 }}
          className={cn(
            isMobile
              ? cn(
                  "absolute inset-y-0 right-0 z-40 w-[min(100%,28rem)] shadow-lg transition-transform duration-200 ease-in-out motion-reduce:transition-none",
                  !panelOpen && "translate-x-full shadow-none",
                )
              : cn(
                  "relative h-full min-h-0 shrink-0 overflow-hidden",
                  !resizing && "transition-[width] duration-200 ease-in-out motion-reduce:transition-none",
                ),
          )}
        >
          {!isMobile && panelOpen ? (
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
              className="absolute inset-y-0 left-0 z-10 w-2 cursor-col-resize focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            />
          ) : null}
          <aside
            id="dataviz-assistant"
            aria-label="Dataviz assistant"
            inert={!panelOpen}
            style={isMobile ? undefined : { width: panel.width }}
            className="flex h-full min-h-0 flex-col border-l bg-background"
          >
            <header className="flex h-14 shrink-0 items-center gap-3 border-b pr-2 pl-4">
              <div className="min-w-0 flex-1">
                <h2 className="truncate text-sm font-medium text-foreground">Dataviz assistant</h2>
                <p className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
                  <span
                    aria-hidden="true"
                    className={cn("size-1.5 shrink-0 rounded-full", dashboardOpen ? "bg-success" : "bg-muted-foreground/40")}
                  />
                  <span className="truncate">
                    {dashboardOpen ? "Can read and edit this dashboard" : "Open a dashboard to work on it together"}
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
                <HeaderAction label="Continue in Agent" onClick={() => onContinueInAgent("")}>
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
                initialPrompt={askPrompt}
              />
            </div>
          </aside>
        </div>
      </div>
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
