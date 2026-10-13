import {
  createParser,
  createStore,
  evaluateElementProps,
} from "@openuidev/lang-core";
import type { ASTNode, ElementNode, OpenUIError } from "@openuidev/lang-core";
import {
  createSchemaLibrary,
  MAX_PROGRAM_BYTES,
  MAX_STATE_BYTES,
  UI_CATALOG_VERSION,
  type EvidenceRows,
} from "./catalog";
import { validateOfflineHtml } from "./html-policy";

export type UiValidation = {
  catalog_version: string;
  state: Record<string, unknown>;
  actions: { id: string; assistant: boolean }[];
  evidence_ids: string[];
  display_text: string;
  media: { type: string; props: Record<string, unknown> }[];
};
const publicComponents = new Set([
  "SearchImage",
  "Image",
  "Map",
  "MapMarker",
  "YouTube",
  "Link",
  "HtmlView",
]);
const textComponents = new Set([
  "Title",
  "Text",
  "Caption",
  "Label",
  "Badge",
  "Markdown",
  "Quote",
  "Flashcard",
  "SvgText",
]);
const interactiveComponents = new Set([
  "Button",
  "IconButton",
  "Pressable",
  "Input",
  "TextArea",
  "Select",
  "SegmentedControl",
  "RadioGroup",
  "Slider",
  "Checkbox",
  "DatePicker",
  "Form",
  "Link",
  "YouTube",
  "Popover",
  "Carousel",
  "Flashcard",
  "Timer",
  "Map",
  "HtmlView",
]);
const isAst = (v: unknown): v is ASTNode =>
  !!v && typeof v === "object" && "k" in v;
const isElement = (v: unknown): v is ElementNode =>
  !!v && typeof v === "object" && "typeName" in v;
const isPlain = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const unsafeKeys = new Set(["__proto__", "constructor", "prototype"]);

export function validateDeclarativeState(
  state: unknown,
  defaults?: Record<string, unknown>,
): asserts state is Record<string, unknown> {
  if (
    !isPlain(state) ||
    JSON.stringify(state).length > MAX_STATE_BYTES ||
    Object.keys(state).length > 64
  )
    throw new Error("UI state exceeds its limit.");
  const visit = (v: unknown, depth: number) => {
    if (depth > 6) throw new Error("UI state is too deeply nested.");
    if (
      v === null ||
      typeof v === "boolean" ||
      (typeof v === "string" && v.length <= 4000) ||
      (typeof v === "number" && Number.isFinite(v))
    )
      return;
    if (Array.isArray(v) && v.length <= 100) {
      v.forEach((x) => visit(x, depth + 1));
      return;
    }
    if (isPlain(v) && Object.keys(v).length <= 50) {
      for (const [k, x] of Object.entries(v)) {
        if (
          unsafeKeys.has(k) ||
          /password|secret|token|credential|result|evidence/i.test(k)
        )
          throw new Error("UI state contains a reserved field.");
        visit(x, depth + 1);
      }
      return;
    }
    throw new Error("Only declarative values can be saved.");
  };
  visit(state, 0);
  if (defaults)
    for (const [key, v] of Object.entries(state)) {
      if (!(key in defaults) || !key.startsWith("$"))
        throw new Error("Undeclared UI state.");
      const base = defaults[key];
      if (
        base !== null &&
        (Array.isArray(base) !== Array.isArray(v) || typeof base !== typeof v)
      )
        throw new Error("UI state type changed.");
    }
}

export function validateProgram(
  program: string,
  evidence: EvidenceRows = {},
  business = false,
  savedState?: Record<string, unknown>,
  privateContext = false,
): UiValidation {
  if (!program || new TextEncoder().encode(program).length > MAX_PROGRAM_BYTES)
    throw new Error("UI program exceeds its limit.");
  const library = createSchemaLibrary(evidence);
  const parsed = createParser(library.toJSONSchema(), "Col").parse(program);
  if (
    parsed.meta.incomplete ||
    parsed.meta.errors.length ||
    parsed.meta.unresolved.length ||
    parsed.meta.orphaned.length ||
    parsed.meta.statementCount > 200 ||
    (!parsed.root && !parsed.rootExpression)
  )
    throw new Error("Invalid or incomplete OpenUI program.");
  if (parsed.queryStatements.length || parsed.mutationStatements.length)
    throw new Error("UI cannot execute tools.");
  validateDeclarativeState(parsed.stateDeclarations);
  if (savedState)
    validateDeclarativeState(savedState, parsed.stateDeclarations);
  const state = { ...parsed.stateDeclarations, ...savedState };
  const evidenceIds = new Set<string>();
  const actions: UiValidation["actions"] = [];
  const media: UiValidation["media"] = [];
  const visible: string[] = [];
  let nodes = 0;
  const schema = library.toJSONSchema();
  const store = createStore();
  store.initialize(parsed.stateDeclarations, state);
  const evalCtx = {
    library,
    store,
    errors: [] as OpenUIError[],
    ctx: {
      getState: (name: string) => store.get(name),
      resolveRef: () => null,
      functions: Object.fromEntries(
        Object.entries(library.functions ?? {}).map(([name, f]) => [
          name,
          { ...schema.functions![name], fn: f.fn },
        ]),
      ),
    },
  };
  const root = parsed.root;
  if (!root || root.typeName !== "Col")
    throw new Error("The UI root must be Col.");
  const ast = (v: unknown, depth = 0): void => {
    if (depth > 40 || ++nodes > 12000)
      throw new Error("UI expression exceeds its limit.");
    if (isAst(v) && v.k === "Comp") {
      if (["Run", "OpenUrl"].includes(v.name))
        throw new Error(
          "Use approved media/link components; direct runtime actions are disabled.",
        );
      if (business && v.name === "Set" && hasEvidence(v.args[1]))
        throw new Error(
          "Verified result data cannot be copied into mutable state.",
        );
      if (v.name === "Evidence") {
        const id = v.args[0];
        if (
          !id ||
          id.k !== "Str" ||
          !Object.prototype.hasOwnProperty.call(evidence, id.v)
        )
          throw new Error("Unknown evidence reference.");
        evidenceIds.add(id.v);
      }
    }
    if (Array.isArray(v)) v.forEach((x) => ast(x, depth + 1));
    else if (v && typeof v === "object")
      Object.values(v).forEach((x) => {
        if (x && typeof x === "object") ast(x, depth + 1);
      });
  };
  const hasEvidence = (v: unknown): boolean => {
    if (isAst(v) && v.k === "Comp" && v.name === "Evidence") return true;
    return Array.isArray(v)
      ? v.some(hasEvidence)
      : !!v &&
          typeof v === "object" &&
          Object.values(v).some((x) => typeof x === "object" && hasEvidence(x));
  };
  const dynamic = (v: unknown): boolean =>
    (isAst(v) && !["Str", "Num", "Bool", "Null", "Arr", "Obj"].includes(v.k)) ||
    (Array.isArray(v)
      ? v.some(dynamic)
      : !!v &&
        typeof v === "object" &&
        Object.values(v).some((x) => typeof x === "object" && dynamic(x)));
  const visit = (v: unknown, depth = 0, insidePressable = false): void => {
    if (depth > 40 || ++nodes > 12000)
      throw new Error("UI tree exceeds its limit.");
    if (
      insidePressable &&
      ((isElement(v) && interactiveComponents.has(v.typeName)) ||
        (isAst(v) && v.k === "Comp" && interactiveComponents.has(v.name)))
    )
      throw new Error(
        "A pressable cannot contain another interactive control.",
      );
    if (isElement(v)) {
      ast(v.props);
      const props = evaluateElementProps(v, evalCtx).props;
      if (
        business &&
        ["Table", "Chart", "Kpi"].includes(v.typeName) &&
        !hasEvidence(v.props.data)
      )
        throw new Error("Business data must bind verified evidence.");
      if (business && ["Table", "Chart", "Kpi"].includes(v.typeName)) {
        const resultTransforms = new Set([
          "Filter",
          "Sort",
          "Take",
          "Slice",
          "Unique",
          "Reverse",
          "Shuffle",
          "Top",
          "Between",
          "Pivot",
          "Find",
        ]);
        const verifiedRows = (x: unknown): boolean => {
          if (!isAst(x)) return false;
          if (x.k === "Ternary")
            return verifiedRows(x.then) && verifiedRows(x.else);
          if (x.k !== "Comp") return false;
          if (x.name === "Evidence") return true;
          if (x.name === "Concat")
            return verifiedRows(x.args[0]) && verifiedRows(x.args[1]);
          return resultTransforms.has(x.name) && verifiedRows(x.args[0]);
        };
        if (!verifiedRows(v.props.data))
          throw new Error(
            "Business data may only filter or transform verified result rows.",
          );
        const inventedRows = (x: unknown): boolean =>
          (isAst(x) && x.k === "Obj") ||
          (Array.isArray(x)
            ? x.some(inventedRows)
            : !!x &&
              typeof x === "object" &&
              Object.values(x).some(inventedRows));
        if (inventedRows(v.props.data))
          throw new Error(
            "Business result rows cannot be constructed from literals.",
          );
      }
      if (publicComponents.has(v.typeName)) {
        if (
          business ||
          privateContext ||
          hasEvidence(v.props) ||
          dynamic(v.props)
        )
          throw new Error(
            "Public media cannot receive business data or form state.",
          );
        if (v.typeName === "Link" && !/^https:\/\//i.test(String(props.url)))
          throw new Error("Only public HTTPS links are allowed.");
        if (
          v.typeName === "Image" &&
          !/^https:\/\/upload\.wikimedia\.org\//.test(String(props.url))
        )
          throw new Error("Images must use Wikimedia uploads.");
        if (v.typeName === "HtmlView") validateOfflineHtml(String(props.html));
        media.push({ type: v.typeName, props });
      }
      if (["Button", "IconButton", "Pressable"].includes(v.typeName)) {
        if (
          typeof props.id !== "string" ||
          !props.id ||
          actions.some((a) => a.id === props.id) ||
          dynamic(v.props.id)
        )
          throw new Error("Action identifiers must be unique literals.");
        const assistantAction = (x: unknown): boolean =>
          (isAst(x) && x.k === "Comp" && x.name === "ToAssistant") ||
          (Array.isArray(x)
            ? x.some(assistantAction)
            : !!x &&
              typeof x === "object" &&
              Object.values(x).some(assistantAction));
        actions.push({
          id: props.id,
          assistant: assistantAction(v.props.action),
        });
      }
      const literals = (x: unknown): void => {
        if (isAst(x) && (x.k === "Str" || x.k === "Num"))
          visible.push(String(x.v));
        else if (Array.isArray(x)) x.forEach(literals);
        else if (x && typeof x === "object")
          Object.values(x).forEach((y) => {
            if (y && typeof y === "object") literals(y);
          });
      };
      for (const key of ["label", "title", "description", "caption", "alt"]) {
        if (typeof props[key] === "string") visible.push(props[key] as string);
        literals(v.props[key]);
      }
      if (
        textComponents.has(v.typeName) ||
        ["Math", "Code"].includes(v.typeName)
      ) {
        for (const key of [
          "text",
          "question",
          "answer",
          "source",
          "formula",
          "code",
        ])
          if (typeof props[key] === "string" || typeof props[key] === "number")
            visible.push(String(props[key]));
        // Inspect literals in every branch, including content hidden by state.
        for (const key of [
          "text",
          "question",
          "answer",
          "formula",
          "code",
          "source",
        ])
          literals(v.props[key]);
      }
      const nested = insidePressable || v.typeName === "Pressable";
      Object.values(v.props).forEach((x) => visit(x, depth + 1, nested));
      // @Each can produce nodes that only exist after evaluation.
      for (const [key, x] of Object.entries(props))
        if (isAst(v.props[key])) visit(x, depth + 1, nested);
      return;
    }
    if (Array.isArray(v))
      v.forEach((x) => visit(x, depth + 1, insidePressable));
    else if (v && typeof v === "object")
      Object.values(v).forEach((x) => {
        if (x && typeof x === "object") visit(x, depth + 1, insidePressable);
      });
  };
  visit(root);
  if (evalCtx.errors.length)
    throw new Error("UI expressions could not evaluate.");
  if (actions.length > 50 || media.length > 25)
    throw new Error("Too many interactive controls.");
  return {
    catalog_version: UI_CATALOG_VERSION,
    state: parsed.stateDeclarations,
    actions,
    evidence_ids: [...evidenceIds],
    display_text: [...new Set(visible)].join("\n"),
    media,
  };
}
