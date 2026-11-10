import { useEffect, useRef, useState } from "react";
import Editor, { type Monaco, type OnMount } from "@monaco-editor/react";
import { useTheme } from "@/context/theme-provider";
import { cn } from "@/lib/utils";
import { applyNovaSqlTheme } from "../monaco-theme";
import type { CellKind } from "./types";

type MonacoEditor = Parameters<OnMount>[0];

const LINE_HEIGHT = 20;
const PADDING = 8;
const MAX_HEIGHT = 560;

export type CellKeyAction = "run" | "run-next" | "run-insert" | "escape";

function heightFor(source: string): number {
  const lines = Math.max(1, source.split("\n").length);
  return Math.min(MAX_HEIGHT, lines * LINE_HEIGHT + PADDING * 2);
}

/**
 * One cell's code editor. Monaco mounts only once the cell is opened for
 * editing, so a notebook with hundreds of cells stays fast; until then the
 * source is shown as text at the same size, and a click or Enter opens it.
 */
export function CellEditor({
  cellId,
  kind,
  value,
  active,
  readOnly,
  onActivate,
  onChange,
  onKey,
}: {
  cellId: string;
  kind: Exclude<CellKind, "markdown"> | "markdown";
  value: string;
  active: boolean;
  readOnly?: boolean;
  onActivate: () => void;
  onChange: (value: string) => void;
  onKey: (action: CellKeyAction) => void;
}) {
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(active);
  const [height, setHeight] = useState(() => heightFor(value));
  const editorRef = useRef<MonacoEditor | null>(null);
  const keyHandler = useRef(onKey);
  keyHandler.current = onKey;

  useEffect(() => {
    if (active) setMounted(true);
  }, [active]);

  useEffect(() => {
    if (active && editorRef.current && !editorRef.current.hasTextFocus()) {
      editorRef.current.focus();
    }
  }, [active, mounted]);

  const language = kind === "python" ? "python" : kind === "sql" ? "sql" : "markdown";

  const handleMount: OnMount = (editor, monaco: Monaco) => {
    editorRef.current = editor;
    applyNovaSqlTheme(monaco, resolvedTheme === "dark");
    editor.onDidContentSizeChange((event) => {
      setHeight(Math.min(MAX_HEIGHT, Math.max(LINE_HEIGHT + PADDING * 2, event.contentHeight)));
    });
    const { KeyMod, KeyCode } = monaco;
    editor.addCommand(KeyMod.Shift | KeyCode.Enter, () => keyHandler.current("run-next"));
    editor.addCommand(KeyMod.CtrlCmd | KeyCode.Enter, () => keyHandler.current("run"));
    editor.addCommand(KeyMod.Alt | KeyCode.Enter, () => keyHandler.current("run-insert"));
    editor.addCommand(
      KeyCode.Escape,
      () => keyHandler.current("escape"),
      "!suggestWidgetVisible && !findWidgetVisible",
    );
    if (active) editor.focus();
  };

  if (!mounted) {
    return (
      <button
        type="button"
        aria-label={`Edit ${kind === "sql" ? "SQL" : kind === "python" ? "Python" : "Markdown"} cell`}
        data-cell-preview={cellId}
        onClick={onActivate}
        onFocus={onActivate}
        className={cn(
          "block w-full cursor-text overflow-hidden text-left font-mono text-xs leading-5 break-words whitespace-pre-wrap text-foreground",
          !value && "text-muted-foreground",
        )}
        style={{ minHeight: height, maxHeight: MAX_HEIGHT, padding: `${PADDING}px 12px` }}
      >
        {value || (kind === "sql" ? "SELECT …" : kind === "python" ? "# Python" : "Text")}
      </button>
    );
  }

  return (
    <div style={{ height }} className="min-w-0" onFocusCapture={onActivate}>
      <Editor
        height="100%"
        language={language}
        theme={resolvedTheme === "dark" ? "nova-dark" : "nova-light"}
        value={value}
        onChange={(next) => onChange(next ?? "")}
        onMount={handleMount}
        options={{
          readOnly,
          minimap: { enabled: false },
          fontSize: 12,
          fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace",
          lineHeight: LINE_HEIGHT,
          lineNumbers: "off",
          glyphMargin: false,
          folding: false,
          lineDecorationsWidth: 12,
          lineNumbersMinChars: 0,
          automaticLayout: true,
          wordWrap: "on",
          scrollBeyondLastLine: false,
          renderLineHighlight: "none",
          overviewRulerLanes: 0,
          overviewRulerBorder: false,
          hideCursorInOverviewRuler: true,
          scrollbar: { alwaysConsumeMouseWheel: false, verticalScrollbarSize: 8 },
          padding: { top: PADDING, bottom: PADDING },
          wordBasedSuggestions: kind === "python" ? "currentDocument" : "off",
          ariaLabel: `${kind === "sql" ? "SQL" : kind === "python" ? "Python" : "Markdown"} cell editor`,
        }}
      />
    </div>
  );
}
