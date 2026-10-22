import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createLibrary,
  defineComponent,
  Renderer,
} from "@openuidev/react-lang";
import type { ActionEvent } from "@openuidev/react-lang";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/features/assistant/markdown";
import { ApiError } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import {
  componentDefinitions,
  createSchemaLibrary,
} from "./catalog";
import { componentDefinitions as legacyDefinitions, createSchemaLibrary as legacyLibrary } from "./catalog-v2";
import { validateProgram as validateLegacyProgram } from "./validator-v2";
import { rendersCatalog } from "./catalog-support";
import { UiNode } from "./components";
import { UiRuntimeContext, type UiArtifacts, type UiArtifactTarget } from "./runtime-context";
import { validateDeclarativeState, validateProgram } from "./validator";
import { uiApi } from "./api";
import { registerUiFlush } from "./state";
import type { UiAction, UiArtifactRequest, UiBlock, UiSource, UiState } from "./types";

// Saved state comes back with object keys sorted, while the client emits them in
// creation order; compare by content so reopening an answer does not re-save it.
// Saving runs in the background; only outcomes that need the user are shown.
const QUIET_STATUS = new Set([
  "Loading saved state…",
  "Unsaved changes",
  "Saving…",
  "Saved",
]);

function sameState(a: unknown, b: unknown) {
  const sorted = (_key: string, value: unknown) =>
    value && typeof value === "object" && !Array.isArray(value)
      ? Object.fromEntries(
          Object.entries(value).sort(([x], [y]) => (x < y ? -1 : x > y ? 1 : 0)),
        )
      : value;
  return JSON.stringify(a, sorted) === JSON.stringify(b, sorted);
}

export function IntelligentUi({
  block,
  source,
  onAction,
  complete,
  onSaveArtifact,
  artifactStatus,
  artifactKinds,
}: {
  block: UiBlock;
  source?: UiSource;
  complete: boolean;
  onAction?: (message: string, action: UiAction) => Promise<void>;
  onSaveArtifact?: (request: UiArtifactRequest) => void;
  artifactStatus?: (request: UiArtifactRequest) => "idle" | "saving" | "saved";
  /** Which visuals offer saving; all of them when omitted. */
  artifactKinds?: UiArtifactRequest["kind"][];
}) {
  const epoch = useAuthStore((s) => s.securityEpoch);
  return (
    <UiSession
      key={`${epoch}:${block.program_sha256}:${JSON.stringify(source)}`}
      block={block}
      source={source}
      complete={complete}
      onAction={onAction}
      onSaveArtifact={onSaveArtifact}
      artifactStatus={artifactStatus}
      artifactKinds={artifactKinds}
    />
  );
}

function sameRecords(a: unknown, b: unknown) {
  return a === b || (Array.isArray(a) && Array.isArray(b) && a.length === b.length
    && JSON.stringify(a) === JSON.stringify(b));
}

function UiSession({
  block,
  source,
  onAction,
  complete,
  onSaveArtifact,
  artifactStatus,
  artifactKinds,
}: Parameters<typeof IntelligentUi>[0]) {
  const readOnly = !source || !onAction;
  // Nova draws deterministic blocks from results already shown; their local
  // controls work without a saved session because they have nothing to send.
  const deterministic = block.provenance.composer === "deterministic";
  const artifacts = useMemo<UiArtifacts | undefined>(() => {
    if (!onSaveArtifact || !artifactStatus) return undefined;
    const ids = block.provenance.evidence_ids;
    const request = (target: UiArtifactTarget): UiArtifactRequest => ({
      ...target,
      columns: Object.keys(block.evidence[target.evidenceId]?.[0] ?? {}),
      toolCallId: block.provenance.tool_calls?.[target.evidenceId],
    });
    return {
      kinds: artifactKinds,
      // Local filters and sorts make new records; one bound result is still their source.
      evidenceFor: (data) =>
        ids.find((id) => sameRecords(block.evidence[id], data))
        ?? (ids.length === 1 ? ids[0] : null),
      save: (target) => onSaveArtifact(request(target)),
      status: (target) => artifactStatus(request(target)),
    };
  }, [artifactKinds, artifactStatus, block, onSaveArtifact]);
  const validation = useMemo(() => {
    try {
      return rendersCatalog(block)
        ? (block.catalog_version === "nova-openui-2" ? validateLegacyProgram : validateProgram)(
            block.program,
            block.evidence,
            block.provenance.evidence_ids.length > 0,
          )
        : null;
    } catch {
      return null;
    }
  }, [block]);
  const library = useMemo(() => {
    const legacy = block.catalog_version === "nova-openui-2";
    const core = (legacy ? legacyLibrary : createSchemaLibrary)(block.evidence);
    return createLibrary({
      root: "Col",
      functions: Object.values(core.functions ?? {}),
      components: (legacy ? legacyDefinitions : componentDefinitions).map((d) =>
        defineComponent({
          ...d,
          component: ({ props }) => <UiNode name={d.name} p={props} />,
        }),
      ),
    });
  }, [block.evidence, block.catalog_version]);
  const [saved, setSaved] = useState<UiState | null>(
    readOnly
      ? {
          revision: 0,
          state: block.state_defaults,
          program_sha256: block.program_sha256,
        }
      : null,
  );
  const [status, setStatus] = useState("Loading saved state…");
  const [renderError, setRenderError] = useState(false);
  const [generation, setGeneration] = useState(0);
  const revision = useRef(0);
  const pending = useRef<Record<string, unknown> | null>(null);
  const latestState = useRef<Record<string, unknown>>(block.state_defaults);
  const saving = useRef<Promise<void> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const blocked = useRef(false);
  const actionId = useRef("");
  const busy = useRef(false);
  const active = useRef(true);
  const sourceKey = JSON.stringify(source);
  const stableSource = useMemo(
    () => (sourceKey ? (JSON.parse(sourceKey) as UiSource) : undefined),
    [sourceKey],
  );

  useEffect(() => {
    active.current = true;
    if (!stableSource || readOnly || !validation || !complete) return;
    const abort = new AbortController();
    void uiApi
      .get(stableSource, abort.signal)
      .then((value) => {
        validateDeclarativeState(value.state, block.state_defaults);
        if (value.program_sha256 !== block.program_sha256)
          throw new Error("UI program changed.");
        if (!abort.signal.aborted) {
          revision.current = value.revision;
          latestState.current = value.state;
          setSaved(value);
          setStatus("Saved");
        }
      })
      .catch(() => {
        if (!abort.signal.aborted) {
          blocked.current = true;
          setStatus("Saved state could not load. Reload to retry.");
        }
      });
    return () => {
      active.current = false;
      abort.abort();
    };
  }, [block, complete, readOnly, stableSource, validation]);

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    if (saving.current) await saving.current;
    if (!pending.current || !stableSource || blocked.current || readOnly) {
      if (pending.current && blocked.current)
        throw new Error("Resolve the saved-state conflict before continuing.");
      return;
    }
    const state = pending.current;
    pending.current = null;
    setStatus("Saving…");
    const task = uiApi
      .put(stableSource, {
        revision: revision.current,
        state,
        program_sha256: block.program_sha256,
      })
      .then((value) => {
        revision.current = value.revision;
        if (active.current) setStatus("Saved");
      })
      .catch((error: unknown) => {
        pending.current ??= state;
        blocked.current = error instanceof ApiError && error.status === 409;
        if (active.current)
          setStatus(
            blocked.current
              ? "Changed on another device. Reload saved state to continue."
              : "Changes could not save. Retry before continuing.",
          );
        throw error;
      })
      .finally(() => {
        saving.current = null;
      });
    saving.current = task;
    await task;
    if (pending.current) await flush();
  }, [block.program_sha256, readOnly, stableSource]);
  useEffect(() => {
    if (readOnly) return;
    const unregister = registerUiFlush(flush);
    const leave = () => {
      void flush().catch(() => {});
    };
    window.addEventListener("pagehide", leave);
    return () => {
      unregister();
      window.removeEventListener("pagehide", leave);
      if (timer.current) clearTimeout(timer.current);
      void flush().catch(() => {});
    };
  }, [flush, readOnly]);

  const update = useCallback(
    (state: Record<string, unknown>) => {
      if (readOnly || !saved || blocked.current) return;
      try {
        validateDeclarativeState(state, block.state_defaults);
      } catch {
        setStatus("This state cannot be saved.");
        blocked.current = true;
        return;
      }
      if (sameState(state, latestState.current)) return;
      pending.current = state;
      latestState.current = state;
      setStatus("Unsaved changes");
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void flush().catch(() => {});
      }, 400);
    },
    [block.state_defaults, flush, readOnly, saved],
  );
  const act = useCallback(
    async (event: ActionEvent) => {
      if (
        event.type !== "continue_conversation" ||
        busy.current ||
        !stableSource ||
        !onAction ||
        blocked.current
      )
        return;
      const id = actionId.current;
      if (!validation?.actions.some((a) => a.id === id && a.assistant)) {
        setStatus("This action is unavailable. Reload the saved answer.");
        return;
      }
      busy.current = true;
      try {
        const state = latestState.current;
        validateDeclarativeState(state, block.state_defaults);
        await flush();
        if (!active.current) return;
        await onAction(
          event.humanFriendlyMessage || "Continue using these inputs.",
          {
            message_id: stableSource.messageId,
            content_id: stableSource.contentId,
            program_sha256: block.program_sha256,
            action_id: id,
            operation_id: crypto.randomUUID(),
            state,
          },
        );
      } catch {
        setStatus("Action could not be sent. Review saved state and retry.");
      } finally {
        busy.current = false;
      }
    },
    [block, flush, onAction, saved, stableSource, validation],
  );

  if (!complete)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Preparing interactive answer…
      </p>
    );
  if (!validation || renderError)
    return (
      <div className="text-sm">
        <p className="mb-2 text-muted-foreground">
          Interactive answer unavailable.
        </p>
        <Markdown offline>{block.fallback}</Markdown>
      </div>
    );
  const reload = async () => {
    if (!stableSource) return;
    try {
      const value = await uiApi.get(stableSource);
      validateDeclarativeState(value.state, block.state_defaults);
      if (value.program_sha256 !== block.program_sha256)
        throw new Error("UI program changed.");
      pending.current = null;
      latestState.current = value.state;
      blocked.current = false;
      revision.current = value.revision;
      setSaved(value);
      setGeneration((v) => v + 1);
      setStatus("Saved");
    } catch {
      setStatus("Saved state could not load.");
    }
  };
  const notice = QUIET_STATUS.has(status) ? "" : status;
  return (
    <section
      className="min-w-0 space-y-3"
      aria-label="Interactive answer"
    >
      {readOnly && !deterministic ? (
        <p className="mb-3 text-xs text-muted-foreground">Read-only preview</p>
      ) : notice || blocked.current ? (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <span
            role="status"
            aria-live="polite"
            className="text-xs text-muted-foreground"
          >
            {notice}
          </span>
          {blocked.current ? (
            <Button
              variant="outline"
              className="min-h-11 border-muted-foreground"
              onClick={() => void reload()}
            >
              Reload saved state
            </Button>
          ) : status.includes("could not save") ? (
            <Button
              variant="outline"
              className="min-h-11 border-muted-foreground"
              onClick={() => void flush().catch(() => {})}
            >
              Retry save
            </Button>
          ) : null}
        </div>
      ) : null}
      {saved ? (
        <UiRuntimeContext.Provider
          value={{
            readOnly: (readOnly && !deterministic) || blocked.current,
            source: stableSource,
            markAction: (id) => {
              actionId.current = id;
            },
            artifacts,
          }}
        >
          <Renderer
            key={generation}
            response={block.program}
            library={library}
            isStreaming={false}
            initialState={saved.state}
            onStateUpdate={update}
            onAction={(event) => {
              void act(event);
            }}
            onError={(errors) => {
              if (errors.length) setRenderError(true);
            }}
            toolProvider={null}
            publishObservability={false}
          />
        </UiRuntimeContext.Provider>
      ) : (
        <Markdown offline>{block.fallback}</Markdown>
      )}
      {block.provenance.composer === "deterministic" ? (
        (block.provenance.omitted_rows ?? 0) > 0 ? (
          <p className="mt-3 text-xs text-muted-foreground">
            {block.provenance.omitted_rows} result rows were omitted from this visual. Ask Nova
            to narrow the query for more detail.
          </p>
        ) : null
      ) : block.provenance.evidence_ids.length ? (
        <p className="mt-3 text-xs text-muted-foreground">
          Within these verified result rows. Inputs are assumptions; filters and
          calculations do not fetch new data.
          {Number.isSafeInteger(block.provenance.omitted_rows) && (block.provenance.omitted_rows ?? 0) > 0
            ? ` ${block.provenance.omitted_rows} result rows were omitted from this visual. Ask Nova to narrow the query for more detail.`
            : ""}
        </p>
      ) : block.provenance.assumption ? (
        <p className="mt-3 text-xs text-muted-foreground">
          Inputs, sample data and calculated values are assumptions.
        </p>
      ) : null}
      {!readOnly && block.provenance.composer !== "deterministic"
        && Object.keys(block.state_defaults).length > 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          Inputs and filters are saved with this conversation in Nova.
          {validation?.actions.some((action) => action.assistant)
            ? " Continuing to the assistant sends submitted values to the configured model provider."
            : " Local calculations do not make a model request."}
        </p>
      )}
    </section>
  );
}
