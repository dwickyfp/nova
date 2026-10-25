import { type ComponentProps, useEffect, useMemo, useRef, useState } from "react";
import Editor, { type Monaco } from "@monaco-editor/react";
import type { Position as MonacoPosition, editor as MonacoEditor } from "monaco-editor";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Loader2, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useTheme } from "@/context/theme-provider";
import { studioApi, type VizSourceBody } from "@/features/agents/api";
import { applyNovaSqlTheme } from "@/features/workspaces/monaco-theme";
import { cn } from "@/lib/utils";
import type {
  CalcCheckResult,
  CalcFunction,
  CalculatedField,
  DataPaneField,
  FieldRef,
} from "./types";
import { pillCaption } from "./worksheet-model";

/**
 * CalcEditor writes one calculated field: a formula editor with field and
 * function completion, a live check against the sheet's fields (type, level,
 * and the exact span of any error), the function reference, and for table
 * calculations the dimensions the calculation runs along.
 */

type MonacoModel = MonacoEditor.ITextModel;

const LANGUAGE = "nova-calc";
const KEYWORDS = [
  "IF",
  "THEN",
  "ELSEIF",
  "ELSE",
  "END",
  "CASE",
  "WHEN",
  "AND",
  "OR",
  "NOT",
  "IN",
  "TRUE",
  "FALSE",
  "NULL",
  "FIXED",
  "INCLUDE",
  "EXCLUDE",
];

type Completions = { fields: string[]; functions: CalcFunction[] };

// Monaco providers are global per language; the open editor publishes what
// they should offer here.
const completionSource: { current: Completions } = { current: { fields: [], functions: [] } };
let languageRegistered = false;

function registerLanguage(monaco: Monaco) {
  if (languageRegistered) return;
  languageRegistered = true;
  monaco.languages.register({ id: LANGUAGE });
  monaco.languages.setMonarchTokensProvider(LANGUAGE, {
    ignoreCase: true,
    keywords: KEYWORDS,
    tokenizer: {
      root: [
        [/\/\/.*$/, "comment"],
        [/\/\*/, "comment", "@comment"],
        [/\[[^\]]*(\]\][^\]]*)*\]/, "type"],
        [/#[^#]*#/, "number"],
        [/'([^']|'')*'/, "string"],
        [/"([^"]|"")*"/, "string"],
        [/\d+(\.\d+)?([eE][+-]?\d+)?/, "number"],
        [/[A-Za-z_]\w*(?=\s*\()/, "predefined"],
        [/[A-Za-z_]\w*/, { cases: { "@keywords": "keyword", "@default": "identifier" } }],
        [/[{}]/, "keyword"],
      ],
      comment: [
        [/\*\//, "comment", "@pop"],
        [/./, "comment"],
      ],
    },
  });
  monaco.languages.setLanguageConfiguration(LANGUAGE, {
    comments: { lineComment: "//", blockComment: ["/*", "*/"] },
    brackets: [
      ["(", ")"],
      ["{", "}"],
      ["[", "]"],
    ],
    autoClosingPairs: [
      { open: "(", close: ")" },
      { open: "{", close: "}" },
      { open: "[", close: "]" },
      { open: "'", close: "'" },
    ],
  });
  monaco.languages.registerCompletionItemProvider(LANGUAGE, {
    triggerCharacters: ["["],
    provideCompletionItems: (model: MonacoModel, position: MonacoPosition) => {
      const word = model.getWordUntilPosition(position);
      const before = model.getLineContent(position.lineNumber).slice(0, position.column - 1);
      const inBracket = /\[[^\]]*$/.exec(before);
      const range = inBracket
        ? new monaco.Range(
            position.lineNumber,
            position.column - inBracket[0].length,
            position.lineNumber,
            position.column,
          )
        : new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn);
      const { fields, functions } = completionSource.current;
      const kinds = monaco.languages.CompletionItemKind;
      const suggestions = [
        ...fields.map((name) => ({
          label: `[${name}]`,
          kind: kinds.Field,
          insertText: `[${name.replace(/\]/g, "]]")}]`,
          range,
          sortText: `0${name}`,
        })),
        ...(inBracket
          ? []
          : functions
              .filter((fn) => fn.kind !== "lod")
              .map((fn) => ({
                label: fn.name,
                kind: kinds.Function,
                detail: fn.syntax,
                documentation: fn.description,
                insertText: `${fn.name}(`,
                range,
                sortText: `1${fn.name}`,
              }))),
        ...(inBracket
          ? []
          : KEYWORDS.map((keyword) => ({
              label: keyword,
              kind: kinds.Keyword,
              insertText: keyword,
              range,
              sortText: `2${keyword}`,
            }))),
      ];
      return { suggestions };
    },
  });
}

const LEVEL_LABELS: Record<string, string> = {
  row: "Row level",
  aggregate: "Aggregate",
  table: "Table calculation",
};

export function CalcEditor({
  open,
  onOpenChange,
  source,
  initial,
  fields,
  viewDimensions,
  onSave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source: VizSourceBody;
  /** The calculation being edited, or null for a new one. */
  initial: CalculatedField | null;
  fields: DataPaneField[];
  viewDimensions: FieldRef[];
  onSave: (calc: CalculatedField, previousName?: string) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [formula, setFormula] = useState(initial?.formula ?? "");
  const [addressing, setAddressing] = useState<string[] | null>(initial?.addressing ?? null);
  const [check, setCheck] = useState<CalcCheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [search, setSearch] = useState("");
  const { resolvedTheme } = useTheme();
  const editorRef = useRef<Parameters<NonNullable<ComponentProps<typeof Editor>["onMount"]>>[0] | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const saveRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    if (!open) return;
    setName(initial?.name ?? "");
    setFormula(initial?.formula ?? "");
    setAddressing(initial?.addressing ?? null);
    setCheck(null);
    setSearch("");
  }, [open, initial]);

  const functions = useQuery({
    queryKey: ["studio", "viz", "functions"],
    queryFn: () => studioApi.vizFunctions(),
    staleTime: Infinity,
    enabled: open,
  });

  const otherFields = useMemo(
    () => fields.filter((field) => field.name !== initial?.name).map((field) => field.name),
    [fields, initial?.name],
  );
  useEffect(() => {
    completionSource.current = {
      fields: otherFields,
      functions: functions.data?.functions ?? [],
    };
  }, [otherFields, functions.data]);

  // Live check, debounced: the server type-checks against real column types.
  useEffect(() => {
    if (!open) return;
    if (!formula.trim()) {
      setCheck(null);
      return;
    }
    let cancelled = false;
    setChecking(true);
    const timer = window.setTimeout(() => {
      studioApi
        .checkVizCalculation({ ...source, formula, name: initial?.name ?? null })
        .then((result) => {
          if (!cancelled) setCheck(result);
        })
        .catch((error: Error) => {
          if (!cancelled) {
            setCheck({ valid: false, error: { message: error.message, start: 0, end: 0 } });
          }
        })
        .finally(() => {
          if (!cancelled) setChecking(false);
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [formula, open, source, initial?.name]);

  // Mark the error span in the editor.
  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    const model = editor?.getModel();
    if (!editor || !monaco || !model) return;
    if (check && !check.valid && check.error.end > check.error.start) {
      const start = model.getPositionAt(check.error.start);
      const end = model.getPositionAt(check.error.end);
      monaco.editor.setModelMarkers(model, "nova-calc", [
        {
          severity: monaco.MarkerSeverity.Error,
          message: check.error.message,
          startLineNumber: start.lineNumber,
          startColumn: start.column,
          endLineNumber: end.lineNumber,
          endColumn: end.column,
        },
      ]);
    } else {
      monaco.editor.setModelMarkers(model, "nova-calc", []);
    }
  }, [check]);

  useEffect(() => {
    if (monacoRef.current) applyNovaSqlTheme(monacoRef.current, resolvedTheme === "dark");
  }, [resolvedTheme]);

  const trimmedName = name.trim();
  const nameTaken =
    trimmedName.length > 0 &&
    fields.some(
      (field) =>
        field.name.toLowerCase() === trimmedName.toLowerCase() &&
        field.name !== initial?.name,
    );
  const canSave = Boolean(trimmedName) && !nameTaken && check?.valid === true && !checking;
  const isTable = check?.valid === true && check.level === "table";

  const reference = useMemo(() => {
    const query = search.trim().toLowerCase();
    const all = functions.data?.functions ?? [];
    const matching = query
      ? all.filter(
          (fn) =>
            fn.name.toLowerCase().includes(query) || fn.description.toLowerCase().includes(query),
        )
      : all;
    const groups = new Map<string, CalcFunction[]>();
    for (const fn of matching) {
      groups.set(fn.category, [...(groups.get(fn.category) ?? []), fn]);
    }
    return [...groups.entries()];
  }, [functions.data, search]);

  const insert = (text: string) => {
    const editor = editorRef.current;
    if (!editor) {
      setFormula((value) => value + text);
      return;
    }
    const selection = editor.getSelection();
    if (selection) {
      editor.executeEdits("nova-calc", [{ range: selection, text, forceMoveMarkers: true }]);
    }
    editor.focus();
  };

  const save = () => {
    if (!canSave) return;
    onSave(
      { name: trimmedName, formula, addressing: isTable ? addressing : null },
      initial?.name,
    );
    onOpenChange(false);
  };
  saveRef.current = save;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] w-full max-w-4xl overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{initial ? "Edit calculated field" : "Create calculated field"}</DialogTitle>
          <DialogDescription>
            Write a formula over the fields in this sheet. Use [Field] for fields, aggregates
            like SUM([Sales]), and {"{FIXED [Region] : SUM([Sales])}"} for a level of detail.
          </DialogDescription>
        </DialogHeader>

        <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_16rem]">
          <div className="flex min-w-0 flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="calc-name">Name</Label>
              <Input
                id="calc-name"
                value={name}
                maxLength={128}
                onChange={(event) => setName(event.target.value)}
                placeholder="Profit ratio"
                aria-invalid={nameTaken || undefined}
              />
              {nameTaken ? (
                <p className="text-xs text-destructive">Another field already has this name.</p>
              ) : null}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="calc-formula">Formula</Label>
              <div
                id="calc-formula"
                className="h-56 overflow-hidden rounded-md border"
                data-testid="calc-formula-editor"
              >
                <Editor
                  language={LANGUAGE}
                  value={formula}
                  onChange={(value) => setFormula(value ?? "")}
                  theme={resolvedTheme === "dark" ? "nova-dark" : "nova-light"}
                  beforeMount={(monaco) => {
                    registerLanguage(monaco);
                    applyNovaSqlTheme(monaco, resolvedTheme === "dark");
                  }}
                  onMount={(editor, monaco) => {
                    editorRef.current = editor;
                    monacoRef.current = monaco;
                    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () =>
                      saveRef.current(),
                    );
                    editor.focus();
                  }}
                  options={{
                    minimap: { enabled: false },
                    fontSize: 13,
                    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                    lineNumbers: "on",
                    wordWrap: "on",
                    scrollBeyondLastLine: false,
                    automaticLayout: true,
                    wordBasedSuggestions: "off",
                    tabSize: 2,
                    ariaLabel: "Calculation formula",
                  }}
                />
              </div>
              <CheckStatus check={check} checking={checking} empty={!formula.trim()} />
            </div>

            {isTable ? (
              <fieldset className="rounded-md border p-3">
                <legend className="px-1 text-xs font-medium">Compute using</legend>
                <p className="mb-2 text-xs text-muted-foreground">
                  The calculation runs along the checked dimensions and restarts for each value of
                  the others.
                </p>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => setAddressing(null)}
                    aria-pressed={addressing === null}
                    className={cn(
                      "rounded-md border px-2 py-1 text-xs focus-visible:outline-2 focus-visible:outline-ring",
                      addressing === null ? "border-ring bg-muted" : "hover:bg-muted",
                    )}
                  >
                    Table (all dimensions)
                  </button>
                  {viewDimensions.map((dimension) => {
                    const checked = addressing?.includes(dimension.field) ?? false;
                    return (
                      <button
                        key={`${dimension.field}-${dimension.date_part ?? ""}`}
                        type="button"
                        aria-pressed={checked}
                        onClick={() =>
                          setAddressing((current) => {
                            const list = current ?? [];
                            return checked
                              ? list.filter((item) => item !== dimension.field)
                              : [...list, dimension.field];
                          })
                        }
                        className={cn(
                          "rounded-md border px-2 py-1 text-xs focus-visible:outline-2 focus-visible:outline-ring",
                          checked ? "border-ring bg-muted" : "hover:bg-muted",
                        )}
                      >
                        {pillCaption(dimension)}
                      </button>
                    );
                  })}
                  {!viewDimensions.length ? (
                    <span className="text-xs text-muted-foreground">
                      Add dimensions to the view to choose a direction.
                    </span>
                  ) : null}
                </div>
              </fieldset>
            ) : null}
          </div>

          <aside aria-label="Function reference" className="flex min-h-0 flex-col gap-2">
            <div className="relative">
              <Search
                aria-hidden="true"
                className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search functions"
                aria-label="Search functions"
                className="h-8 pl-7 text-xs"
              />
            </div>
            <div className="max-h-80 overflow-y-auto rounded-md border p-1">
              {functions.isLoading ? (
                <p className="p-2 text-xs text-muted-foreground">Loading functions…</p>
              ) : null}
              {reference.map(([category, items]) => (
                <div key={category} className="mb-1">
                  <p className="px-2 pt-1.5 pb-1 text-xs font-medium text-muted-foreground">
                    {category}
                  </p>
                  <ul>
                    {items.map((fn) => (
                      <li key={fn.name}>
                        <button
                          type="button"
                          onClick={() =>
                            insert(fn.kind === "lod" ? `{${fn.name} [] : }` : `${fn.name}(`)
                          }
                          title={`${fn.syntax}\n${fn.description}`}
                          className="w-full rounded px-2 py-1 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
                        >
                          <span className="block font-mono text-xs">{fn.name}</span>
                          <span className="block truncate text-xs text-muted-foreground">
                            {fn.description}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              {!functions.isLoading && !reference.length ? (
                <p className="p-2 text-xs text-muted-foreground">No function matches.</p>
              ) : null}
            </div>
          </aside>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={save} disabled={!canSave}>
            {initial ? "Save calculation" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CheckStatus({
  check,
  checking,
  empty,
}: {
  check: CalcCheckResult | null;
  checking: boolean;
  empty: boolean;
}) {
  if (empty) {
    return <p className="text-xs text-muted-foreground">Start typing a formula.</p>;
  }
  if (checking && !check) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
        <Loader2 aria-hidden="true" className="size-3 animate-spin" /> Checking…
      </p>
    );
  }
  if (!check) return null;
  if (!check.valid) {
    return (
      <p className="flex items-start gap-1.5 text-xs text-destructive" role="alert">
        <AlertCircle aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
        {check.error.message}
      </p>
    );
  }
  return (
    <p className="flex items-center gap-1.5 text-xs text-success-strong" role="status">
      <CheckCircle2 aria-hidden="true" className="size-3.5" />
      The calculation is valid · {LEVEL_LABELS[check.level] ?? check.level} ·{" "}
      {check.data_type === "unknown" ? "any type" : check.data_type}
      {checking ? <Loader2 aria-hidden="true" className="size-3 animate-spin" /> : null}
    </p>
  );
}
