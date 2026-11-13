import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Editor, { type Monaco, type OnMount } from "@monaco-editor/react";
import hljs from "highlight.js/lib/core";
import sql from "highlight.js/lib/languages/sql";
import python from "highlight.js/lib/languages/python";
import { useTheme } from "@/context/theme-provider";
import { cn } from "@/lib/utils";
import { applyNovaCellTheme } from "../monaco-theme";
import type { CellKind } from "./types";

type MonacoEditor = Parameters<OnMount>[0];

hljs.registerLanguage("sql", sql);
hljs.registerLanguage("python", python);

const LINE_HEIGHT = 20;
const PADDING = 8;
const MAX_HEIGHT = 560;
/** Width of the line-number gutter, shared by Monaco and the static preview. */
const GUTTER_CHARS = 3;

export type CellKeyAction = "run" | "run-next" | "run-insert" | "escape";

function heightFor(source: string): number {
  const lines = Math.max(1, source.split("\n").length);
  return Math.min(MAX_HEIGHT, lines * LINE_HEIGHT + PADDING * 2);
}

/**
 * One cell's code editor. Monaco mounts only once the cell is opened for
 * editing, so a notebook with hundreds of cells stays fast; until then the
 * source is syntax-highlighted at the same size; a click or Enter opens it.
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
  const monacoRef = useRef<Monaco | null>(null);
  const appliedDark = useRef<boolean | null>(null);
  const [themeName, setThemeName] = useState(resolvedTheme === "dark" ? "vs-dark" : "vs");

  // Follow the page's own light/dark class (what its colors come from), and
  // rebuild the cell theme from its tokens whenever that class changes.
  const syncTheme = useCallback(() => {
    const monaco = monacoRef.current;
    if (!monaco) return;
    const pageDark = document.documentElement.classList.contains("dark");
    if (appliedDark.current === pageDark) return;
    appliedDark.current = pageDark;
    setThemeName(applyNovaCellTheme(monaco, pageDark));
  }, []);

  useEffect(() => {
    const observer = new MutationObserver(syncTheme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, [syncTheme]);
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

  const highlighted = useMemo(() => {
    if (mounted || !value || kind === "markdown") return null;
    return hljs.highlight(value, { language }).value;
  }, [mounted, value, kind, language]);

  const handleMount: OnMount = (editor, monaco: Monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    appliedDark.current = null;
    syncTheme();
    editor.onDidContentSizeChange((event) => {
      setHeight(Math.min(MAX_HEIGHT, Math.max(LINE_HEIGHT + PADDING * 2, event.contentHeight)));
    });
    const { KeyMod, KeyCode } = monaco;
    editor.addCommand(KeyMod.Shift | KeyCode.Enter, () => keyHandler.current("run-next"));
    editor.addCommand(KeyMod.Alt | KeyCode.Enter, () => keyHandler.current("run-insert"));
    editor.addCommand(
      KeyCode.Escape,
      () => keyHandler.current("escape"),
      "!suggestWidgetVisible && !findWidgetVisible",
    );
    if (active) editor.focus();
  };

  if (!mounted) {
    const lines = (value || (kind === "sql" ? "SELECT …" : kind === "python" ? "# Python" : "Text")).split("\n");
    return (
      <button
        type="button"
        aria-label={`Edit ${kind === "sql" ? "SQL" : kind === "python" ? "Python" : "Markdown"} cell`}
        data-cell-preview={cellId}
        onClick={onActivate}
        onFocus={onActivate}
        className={cn(
          "grid w-full cursor-text grid-cols-[auto_1fr] overflow-hidden rounded-md bg-muted text-left font-mono text-xs leading-5",
          !value && "text-muted-foreground",
        )}
        style={{ minHeight: height, maxHeight: MAX_HEIGHT, paddingBlock: PADDING }}
      >
        <span
          aria-hidden
          className="pr-3 pl-2 text-right text-muted-foreground tabular-nums select-none"
          style={{ minWidth: `calc(${GUTTER_CHARS}ch + 20px)` }}
        >
          {lines.map((_, index) => (
            <span key={index} className="block">
              {index + 1}
            </span>
          ))}
        </span>
        {highlighted !== null ? (
          <span
            className="hljs min-w-0 pr-3 break-words whitespace-pre-wrap text-foreground"
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        ) : (
          <span className="min-w-0 pr-3 break-words whitespace-pre-wrap text-foreground">
            {lines.map((line, index) => (
              <span key={index} className="block min-h-5">{line}</span>
            ))}
          </span>
        )}
      </button>
    );
  }

  return (
    <div style={{ height }} className="min-w-0 overflow-hidden rounded-md bg-muted" onFocusCapture={onActivate}>
      <Editor
        height="100%"
        language={language}
        theme={themeName}
        value={value}
        onChange={(next) => onChange(next ?? "")}
        onMount={handleMount}
        options={{
          readOnly,
          minimap: { enabled: false },
          fontSize: 12,
          fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace",
          lineHeight: LINE_HEIGHT,
          lineNumbers: "on",
          glyphMargin: false,
          folding: false,
          lineDecorationsWidth: 12,
          lineNumbersMinChars: GUTTER_CHARS,
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
