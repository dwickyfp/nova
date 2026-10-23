import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { IntelligentUi } from "./renderer";
import { validateProgram } from "./validator";
import { createSchemaLibrary, componentDefinitions } from "./catalog";
import { uiFunctions } from "./functions";
import { uiApi } from "./api";
import { flushUiStates } from "./state";
import type { UiBlock, UiSource } from "./types";
import "@/styles/index.css";
import { ThemeProvider } from "@/context/theme-provider";

const source: UiSource = {
  agentId: "agent",
  threadId: "thread",
  messageId: "message",
  contentId: "content-1",
};
function block(program: string): UiBlock {
  const result = validateProgram(program);
  return {
    catalog_version: result.catalog_version,
    program_sha256: "a".repeat(64),
    program,
    fallback: "Use this interactive workspace.",
    state_defaults: result.state,
    actions: result.actions,
    evidence: {},
    provenance: {
      evidence_ids: [],
      scope: "user_simulation",
      assumption: true,
    },
  };
}

let originalTheme = "";
beforeEach(() => {
  vi.spyOn(uiApi, "mapMetric").mockResolvedValue({});
  originalTheme = document.documentElement.className;
  vi.spyOn(uiApi, "get").mockImplementation(async () => ({
    revision: 0,
    state: {},
    program_sha256: "a".repeat(64),
  }));
  vi.spyOn(uiApi, "put").mockImplementation(async (_, value) => ({
    ...value,
    revision: value.revision + 1,
  }));
});
afterEach(async () => {
  await flushUiStates().catch(() => {});
  document.documentElement.className = originalTheme;
  vi.restoreAllMocks();
});

it("discloses result rows omitted by the server's evidence transport", async () => {
  const program = 'root = Col([Table(@Evidence("e1"))])';
  const evidence = { e1: [{ name: "Fixture" }] };
  const parsed = validateProgram(program, evidence, true);
  const ui: UiBlock = { ...parsed, program, program_sha256: "a".repeat(64),
    fallback: "Fixture results.", state_defaults: parsed.state, evidence,
    provenance: { scope: "verified_result_rows", assumption: false, evidence_ids: ["e1"], omitted_rows: 7 } };
  const screen = await render(<IntelligentUi block={ui} complete />);
  await expect.element(screen.getByText(/7 result rows were omitted from this visual/)).toBeVisible();
});

it("saves a bound chart or table with its source query and the result's own columns", async () => {
  const program = 'root = Col([Chart(@Sort(@Evidence("e1"), "revenue", "desc"), "month", "revenue", "line", "Revenue"), Table(@Evidence("e1"), ["month"], "Months")])';
  const evidence = { e1: [{ month: "Jan", revenue: 10 }, { month: "Feb", revenue: 30 }] };
  const parsed = validateProgram(program, evidence, true);
  const ui: UiBlock = { ...parsed, program, program_sha256: "b".repeat(64),
    fallback: "Revenue.", state_defaults: parsed.state, evidence,
    provenance: { scope: "verified_result_rows", assumption: false, evidence_ids: ["e1"],
      tool_calls: { e1: "query-1" } } };
  const onSaveArtifact = vi.fn();
  const screen = await render(
    <IntelligentUi block={ui} complete onSaveArtifact={onSaveArtifact}
      artifactStatus={(request) => (request.kind === "table" ? "saved" : "idle")} />,
  );
  await screen.getByRole("button", { name: "Save chart as artifact" }).click();
  expect(onSaveArtifact).toHaveBeenCalledWith({
    kind: "chart",
    title: "Revenue",
    chart: { type: "line", x: "month", y: "revenue", series: ["revenue"] },
    evidenceId: "e1",
    columns: ["month", "revenue"],
    toolCallId: "query-1",
  });
  await expect.element(screen.getByRole("button", { name: "Artifact saved" })).toBeDisabled();
  await screen.unmount();

  const chartsOnly = await render(
    <IntelligentUi block={ui} complete onSaveArtifact={onSaveArtifact}
      artifactStatus={() => "idle"} artifactKinds={["chart"]} />,
  );
  await expect
    .element(chartsOnly.getByRole("button", { name: "Save chart as artifact" }))
    .toBeVisible();
  expect(chartsOnly.getByRole("button", { name: "Save table as artifact" }).query()).toBeNull();
});

describe("Nova interactive answers", () => {
  it("dispatches a conditional follow-up after local state reveals it", async () => {
    const ui = block('$show=false\nroot=Col([Button("Calculate",@Set($show,true),"calculate"),$show ? Button("Ask agent",@ToAssistant("Explain"),"ask") : Text("Ready")])');
    ui.actions = [];
    const onAction = vi.fn();
    const screen = await render(<IntelligentUi block={ui} source={source} complete onAction={onAction} />);
    await expect.element(screen.getByRole("button", { name: "Ask agent" })).not.toBeInTheDocument();
    await screen.getByRole("button", { name: "Calculate" }).click();
    await screen.getByRole("button", { name: "Ask agent" }).click();
    await vi.waitFor(() => expect(onAction).toHaveBeenCalledTimes(1));
    expect(onAction).toHaveBeenCalledWith("Explain", expect.objectContaining({
      action_id: "ask", state: { $show: true }, program_sha256: ui.program_sha256,
    }));
  });
  it("does not dispatch an action after leaving its conversation during save", async () => {
    const ui = block(
      'root = Col([Input($units,"Units",null,false,"number"), Button("Continue",@ToAssistant("Continue"),"continue")])\n$units=5',
    );
    vi.mocked(uiApi.get).mockResolvedValue({
      revision: 0,
      state: { $units: 5 },
      program_sha256: ui.program_sha256,
    });
    let finish!: (value: {
      revision: number;
      state: Record<string, unknown>;
      program_sha256: string;
    }) => void;
    vi.mocked(uiApi.put).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const onAction = vi.fn();
    const screen = await render(
      <IntelligentUi block={ui} source={source} complete onAction={onAction} />,
    );
    await screen.getByRole("spinbutton", { name: "Units" }).fill("6");
    await screen.getByRole("button", { name: "Continue" }).click();
    await vi.waitFor(() => expect(uiApi.put).toHaveBeenCalledTimes(1));
    await screen.rerender(<IntelligentUi block={ui} complete />);
    finish({
      revision: 1,
      state: { $units: 6 },
      program_sha256: ui.program_sha256,
    });
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(onAction).not.toHaveBeenCalled();
  });

  it("saves the current value after an edit is reverted before autosave", async () => {
    const original = window.setTimeout.bind(window);
    vi.spyOn(window, "setTimeout").mockImplementation((fn, delay, ...args) =>
      original(fn, delay === 400 ? 60_000 : delay, ...args),
    );
    const ui = block(
      'root = Col([Input($units,"Units",null,false,"number")])\n$units=5',
    );
    vi.mocked(uiApi.get).mockResolvedValue({
      revision: 0,
      state: { $units: 5 },
      program_sha256: ui.program_sha256,
    });
    const screen = await render(
      <IntelligentUi block={ui} source={source} complete onAction={vi.fn()} />,
    );
    const input = screen.getByRole("spinbutton", { name: "Units" });
    await input.fill("6");
    await input.fill("5");
    await flushUiStates();
    expect(uiApi.put).toHaveBeenLastCalledWith(
      source,
      expect.objectContaining({ state: { $units: 5 } }),
    );
  });

  it("pins the full component and function catalog and rejects unsafe sources", () => {
    expect(componentDefinitions).toHaveLength(57);
    expect(uiFunctions).toHaveLength(59);
    expect(() => validateProgram('root = Col([Unknown("x")])')).toThrow();
    expect(() =>
      validateProgram(
        'root = Col([Pressable("Outer",@Set($n,1),"outer",[Button("Inner",@ToAssistant("Continue"),"inner")])])\n$n=0',
      ),
    ).toThrow();
    expect(() =>
      validateProgram('root = Col([Table(@Evidence("fake"))])'),
    ).toThrow();
    expect(() =>
      validateProgram(
        'root = Col([SearchImage($query,"photo")])\n$query="Bali"',
      ),
    ).toThrow();
    expect(() =>
      validateProgram(
        `root = Col([HtmlView("<script>fetch('https://example.com')</script>","Game")])`,
      ),
    ).toThrow();
    expect(() =>
      validateProgram("root = Col([Table([{revenue:42}])])", {}, true),
    ).toThrow();
    const functions = createSchemaLibrary().functions!;
    expect(
      functions.Payment.fn({ principal: 1200, rate: 0, periods: 12 }),
    ).toBe(100);
    expect(functions.MovingAvg.fn({ values: [2, 4, 6], window: 2 })).toEqual([
      2, 3, 5,
    ]);
    expect(functions.AddMonths.fn({ date: "2026-01-31", months: 1 })).toBe(
      "2026-02-28",
    );
    expect(
      functions.Pivot.fn({
        rows: [
          { region: "East", category: "sales", amount: "12.50" },
          { region: "East", category: "sales", amount: "7.25" },
        ],
        group: "region",
        category: "category",
        value: "amount",
      }),
    ).toEqual([{ region: "East", sales: 19.75 }]);
    expect(
      functions.Stats.fn({ values: ["12.50", "7.25", null, "missing"] }),
    ).toMatchObject({ count: 2, sum: 19.75 });
    expect(() =>
      functions.Top.fn({
        rows: [{ amount: "9007199254740993" }, { amount: "12.50" }],
        field: "amount",
        count: 1,
      }),
    ).toThrow();
  });

  it("calculates locally, saves, flushes and sends the source-bound action once", async () => {
    const ui = block(
      'root = Col([Title("Assumptions calculator"), Input($units,"Units",null,false,"number"), Text("Total: "+($units*20)), Checkbox($ready,"Ready"), Button("Continue",@ToAssistant("Use these assumptions"),"continue")])\n$units=5\n$ready=false',
    );
    vi.mocked(uiApi.get).mockResolvedValue({
      revision: 2,
      state: { $units: 5, $ready: false },
      program_sha256: ui.program_sha256,
    });
    const onAction = vi.fn().mockResolvedValue(undefined);
    const screen = await render(
      <IntelligentUi block={ui} source={source} complete onAction={onAction} />,
    );
    await expect.element(screen.getByText("Total: 100")).toBeVisible();
    await screen.getByRole("spinbutton", { name: "Units" }).fill("7");
    await expect.element(screen.getByText("Total: 140")).toBeVisible();
    await screen.getByRole("checkbox", { name: "Ready" }).click();
    await flushUiStates();
    const writes = vi.mocked(uiApi.put).mock.calls;
    expect(writes.map(([, input]) => input.revision)).toEqual(
      writes.map((_, index) => 2 + index),
    );
    expect(uiApi.put).toHaveBeenLastCalledWith(
      source,
      expect.objectContaining({
        revision: 2 + writes.length - 1,
        state: { $units: 7, $ready: true },
      }),
    );
    await screen.getByRole("button", { name: "Continue" }).click();
    await vi.waitFor(() => expect(onAction).toHaveBeenCalledTimes(1));
    expect(onAction).toHaveBeenCalledWith(
      "Use these assumptions",
      expect.objectContaining({
        action_id: "continue",
        message_id: "message",
        state: { $units: 7, $ready: true },
      }),
    );
  });

  it("restores another device state and exposes a revision conflict without overwriting it", async () => {
    const ui = block(
      'root = Col([Input($name,"Name"), Text("Hello "+$name)])\n$name=""',
    );
    vi.mocked(uiApi.get).mockResolvedValue({
      revision: 4,
      state: { $name: "Ada" },
      program_sha256: ui.program_sha256,
    });
    const screen = await render(
      <IntelligentUi block={ui} source={source} complete onAction={vi.fn()} />,
    );
    await expect.element(screen.getByText("Hello Ada")).toBeVisible();
    const { ApiError } = await import("@/lib/api-client");
    vi.mocked(uiApi.put).mockRejectedValue(
      new ApiError(409, "Changed on another device"),
    );
    await screen.getByRole("textbox", { name: "Name" }).fill("Grace");
    await expect(flushUiStates()).rejects.toThrow();
    await expect
      .element(
        screen.getByText(
          "Changed on another device. Reload saved state to continue.",
        ),
      )
      .toBeVisible();
    vi.mocked(uiApi.get).mockResolvedValue({
      revision: 5,
      state: { $name: "Lin" },
      program_sha256: ui.program_sha256,
    });
    await screen.getByRole("button", { name: "Reload saved state" }).click();
    await expect.element(screen.getByText("Hello Lin")).toBeVisible();
  });

  it("renders form validation, filtered chart, SVG, flashcards and offline canvas", async () => {
    const ui = block(
      'root = Col([Form("signup",[Input($email,"Email",null,true,"email"), Button("Submit",@ToAssistant("Review this form"),"submit")]), Select($metric,"Metric",["a","b"]), Chart([{label:"One",a:10,b:20}],"label",$metric), Svg([Rect(10,10,100,50,"primary"),SvgText(15,40,"Diagram","on-primary")],"0 0 160 80","Example diagram"), Flashcard("Question","Answer"), HtmlView("<button id=play>Play</button><canvas id=game width=200 height=100></canvas><script>document.getElementById(\'play\').onclick=()=>{const c=document.getElementById(\'game\');c.dataset.played=\'yes\';c.getContext(\'2d\').fillRect(5,5,20,20)}</script>","Canvas game")])\n$email=""\n$metric="a"',
    );
    vi.mocked(uiApi.get).mockResolvedValue({
      revision: 0,
      state: ui.state_defaults,
      program_sha256: ui.program_sha256,
    });
    const onAction = vi.fn();
    const screen = await render(
      <IntelligentUi block={ui} source={source} complete onAction={onAction} />,
    );
    await expect
      .element(screen.getByRole("textbox", { name: "Email" }))
      .toBeVisible();
    await screen.getByRole("button", { name: "Submit" }).click();
    expect(onAction).not.toHaveBeenCalled();
    await screen.getByRole("combobox", { name: "Metric" }).selectOptions("b");
    await screen.getByText("View chart data").click();
    await expect
      .element(screen.getByRole("cell", { name: "20", exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByRole("img", { name: "Example diagram" }))
      .toBeVisible();
    await screen.getByRole("button", { name: "Show answer" }).click();
    await expect
      .element(screen.getByRole("button", { name: "Show question" }))
      .toBeVisible();
    const iframe = screen.container.querySelector("iframe")!;
    expect(iframe.sandbox.value).toBe("allow-scripts");
    expect(iframe.srcdoc).toContain("connect-src 'none'");
    expect(iframe.srcdoc).toContain("form-action 'none'");
    await userEvent.tab();
    expect(document.activeElement).not.toBe(document.body);
  });

  it.each(
    ["bar", "line", "area", "pie", "donut", "scatter"].flatMap((type) =>
      ["light", "dark"].map((theme) => [type, theme]),
    ),
  )(
    "renders a %s chart in %s with contrast boundaries and accessible data",
    async (type, theme) => {
      document.documentElement.classList.toggle("dark", theme === "dark");
      const ui = block(
        `root = Col([Chart([{x:1,a:10,b:20},{x:2,a:5,b:15}],"x","a","${type}","Example values",["a","b"])])`,
      );
      const screen = await render(<IntelligentUi block={ui} complete />);
      await expect.element(screen.getByText("Example values")).toBeVisible();
      await screen.getByText("View chart data").click();
      await expect
        .element(screen.getByRole("cell", { name: "10", exact: true }))
        .toBeVisible();
      await expect
        .element(screen.getByRole("cell", { name: "20", exact: true }))
        .toBeVisible();
      await vi.waitFor(() =>
        expect(
          screen.container.querySelector(
            ".recharts-surface path, .recharts-surface rect",
          ),
        ).not.toBeNull(),
      );
      const paint = (css: string) => {
        const ctx = document.createElement("canvas").getContext("2d")!;
        ctx.fillStyle = css;
        return ctx.fillStyle;
      };
      const edge = getComputedStyle(document.documentElement)
        .getPropertyValue(theme === "dark" ? "--card" : "--foreground")
        .trim();
      const shapes = [
        ...screen.container.querySelectorAll<SVGElement>(
          ".recharts-line-curve, .recharts-area-curve, .recharts-sector, .recharts-rectangle, .recharts-scatter-symbol path",
        ),
      ];
      expect(
        shapes.some(
          (shape) => paint(getComputedStyle(shape).stroke) === paint(edge),
        ),
      ).toBe(true);
    },
  );

  it("does not execute an unfinished program and renders a read-only trace", async () => {
    const ui = block(
      'root = Col([Input($n,"Value"),Button("Add",@Set($n,$n+1),"add")])\n$n=1',
    );
    const screen = await render(<IntelligentUi block={ui} complete={false} />);
    await expect
      .element(screen.getByText("Preparing interactive answer…"))
      .toBeVisible();
    expect(screen.container.querySelector("input")).toBeNull();
    await screen.rerender(<IntelligentUi block={ui} complete />);
    await expect
      .element(screen.getByRole("button", { name: "Add" }))
      .toBeDisabled();
    expect(uiApi.put).not.toHaveBeenCalled();
  });

  it("keeps Markdown media offline and local form actions execute once", async () => {
    const ui = block(
      'root = Col([Markdown("![Private](https://example.com/image) [Private link](https://example.com)"), Form("local",[Text("Count: "+$n), Button("Increment",@Set($n,$n+1),"increment")])])\n$n=0',
    );
    vi.mocked(uiApi.get).mockResolvedValue({
      revision: 0,
      state: ui.state_defaults,
      program_sha256: ui.program_sha256,
    });
    const screen = await render(
      <IntelligentUi block={ui} source={source} complete onAction={vi.fn()} />,
    );
    await expect.element(screen.getByText("Count: 0")).toBeVisible();
    expect(screen.container.querySelector("img")).toBeNull();
    expect(screen.container.querySelector("a[href]")).toBeNull();
    await screen.getByRole("button", { name: "Increment" }).click();
    await expect.element(screen.getByText("Count: 1")).toBeVisible();
  });
});


it("routes public images to media and retains their offline fallback", async () => {
  vi.spyOn(uiApi, "media").mockRejectedValue(new Error("Offline"));
  const screen = await render(<IntelligentUi
    block={block('root = Col([SearchImage("London","London landmark")])')}
    complete source={source} onAction={async () => {}} />);
  await expect.element(screen.getByText(/Public media could not load/)).toBeVisible();
  expect(uiApi.media).toHaveBeenCalledWith(source, "SearchImage", expect.objectContaining({query:"London"}), expect.any(AbortSignal));
});

it("renders verified geometry and links table selection to declarative state", async () => {
  const program = 'root = Col([GeoMap(@Evidence("branches"),null,null,"shape","name","Verified branches",$selected)])\n$selected=-1';
  const evidence = {branches:[{name:"Jakarta",shape:"POINT (106.8 -6.2)"}, {name:"Missing",shape:null}]};
  const parsed = validateProgram(program,evidence,true);
  const ui: UiBlock = {...parsed,program,program_sha256:"a".repeat(64),fallback:"One mapped location.",
    state_defaults:parsed.state,evidence,provenance:{scope:"business",assumption:false,evidence_ids:["branches"]}};
  const screen = await render(<ThemeProvider><IntelligentUi block={ui} complete source={source} onAction={async () => {}} /></ThemeProvider>);
  await expect.element(screen.getByText(/1 mapped; 1 invalid; 0 omitted/)).toBeVisible();
  const summary=screen.getByText("View locations",{exact:true});
  await summary.click();
  await screen.getByRole("button",{name:"Select Jakarta"}).click();
  await expect.element(screen.getByText("Selected: Jakarta",{exact:true})).toBeVisible();
  await expect.element(screen.getByLabelText("Map viewport")).toHaveAttribute("aria-busy", "false");
  await expect.element(screen.getByRole("button",{name:"Select Jakarta"})).toBeVisible();
  await expect.element(screen.getByText("Invalid or missing coordinates; excluded from map")).toBeVisible();
  await flushUiStates();
  expect(uiApi.put).toHaveBeenCalledWith(source,expect.objectContaining({state:{$selected:0}}));
});

it("saves the camera after the user moves a verified map", async () => {
  const program = '$selected=-1\n$viewport=null\nroot = Col([GeoMap(@Evidence("branches"),"lon","lat",null,"name","Verified branches",$selected,{advanced:true},$viewport)])';
  const evidence = {branches:[{name:"Jakarta",lon:106.8,lat:-6.2},{name:"Surabaya",lon:112.7,lat:-7.3}]};
  const parsed = validateProgram(program,evidence,true);
  const ui: UiBlock = {...parsed,program,program_sha256:"b".repeat(64),fallback:"Two mapped locations.",
    state_defaults:parsed.state,evidence,provenance:{scope:"verified_result_rows",assumption:false,evidence_ids:["branches"]}};
  vi.mocked(uiApi.get).mockResolvedValue({revision:0,state:ui.state_defaults,program_sha256:ui.program_sha256});
  const screen = await render(<ThemeProvider><IntelligentUi block={ui} complete source={source} onAction={async () => {}} /></ThemeProvider>);
  await expect.element(screen.getByLabelText("Map viewport")).toHaveAttribute("aria-busy", "false");
  // Saving runs in the background without a progress label.
  const seen: string[] = [];
  const observer = new MutationObserver(() => seen.push(screen.container.textContent ?? ""));
  observer.observe(screen.container, {subtree:true, childList:true, characterData:true});
  await screen.getByRole("button",{name:"Show all locations"}).click();
  await vi.waitFor(() => expect(uiApi.put).toHaveBeenCalledWith(source,expect.objectContaining({
    state: expect.objectContaining({$viewport: expect.objectContaining({center: expect.any(Array)})}),
  })), {timeout: 3000});
  observer.disconnect();
  expect(seen.join(" ")).not.toMatch(/Unsaved changes|Saving…/);
});

it("does not re-save a restored camera whose keys come back in another order", async () => {
  const program = '$selected=-1\n$viewport=null\nroot = Col([GeoMap(@Evidence("branches"),"lon","lat",null,"name","Verified branches",$selected,{},$viewport)])';
  const evidence = {branches:[{name:"Jakarta",lon:106.8,lat:-6.2},{name:"Surabaya",lon:112.7,lat:-7.3}]};
  const parsed = validateProgram(program,evidence,true);
  const ui: UiBlock = {...parsed,program,program_sha256:"c".repeat(64),fallback:"Two mapped locations.",
    state_defaults:parsed.state,evidence,provenance:{scope:"verified_result_rows",assumption:false,evidence_ids:["branches"]}};
  vi.mocked(uiApi.get).mockResolvedValue({revision:4,program_sha256:ui.program_sha256,
    state:{$selected:-1,$viewport:{bearing:0,center:[109.75,-6.75],pitch:0,zoom:5}}});
  const screen = await render(<ThemeProvider><IntelligentUi block={ui} complete source={source} onAction={async () => {}} /></ThemeProvider>);
  await expect.element(screen.getByLabelText("Map viewport")).toHaveAttribute("aria-busy", "false");
  await new Promise((resolve) => setTimeout(resolve, 800));
  await flushUiStates();
  expect(uiApi.put).not.toHaveBeenCalled();
});
