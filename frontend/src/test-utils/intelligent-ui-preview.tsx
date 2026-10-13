import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "@/context/theme-provider";
import { IntelligentUi } from "@/features/studio/intelligent-ui/renderer";
import { validateProgram } from "@/features/studio/intelligent-ui/validator";
import { uiApi } from "@/features/studio/intelligent-ui/api";
import { flushUiStates } from "@/features/studio/intelligent-ui/state";
import type { UiBlock } from "@/features/studio/intelligent-ui/types";
import "@/styles/index.css";
import { StudioChat } from "@/features/studio/studio-chat";
import type { Agent } from "@/features/agents/api";
import { useAuthStore } from "@/stores/auth-store";

const theme =
  new URLSearchParams(location.search).get("theme") === "dark"
    ? "dark"
    : "light";
document.documentElement.className = theme;
const html = `<button id="play">Play canvas game</button><p id="score">Score: 0</p><canvas id="game" width="240" height="120"></canvas><script>let score=0;document.getElementById('play').onclick=()=>{score++;document.getElementById('score').textContent='Score: '+score;const canvas=document.getElementById('game');const ctx=canvas.getContext('2d');ctx.fillStyle=getComputedStyle(canvas).color;ctx.fillRect(5,5,score*10,20)}</script>`;
const program = `root = Col([Title("Plan with assumptions"),Text("Adjust inputs, review the result, then ask Nova to continue."),Grid([Card("Calculator",[Input($units,"Units",null,false,"number"),Text("Total: "+($units*20)),Checkbox($ready,"Checklist complete"),Slider($rate,"Rate",1,10)]),Card("Review form",[Form("review",[Input($email,"Email",null,true,"email"),TextArea($notes,"Notes"),Button("Review with Nova",@ToAssistant("Review my inputs"),"review")])])]),Select($metric,"Chart measure",["a","b"]),Select($area,"Chart filter",["All","Example A","Example B"]),Chart($area=="All"?[{label:"Example A",a:10,b:20},{label:"Example B",a:5,b:15}]:@Filter([{label:"Example A",a:10,b:20},{label:"Example B",a:5,b:15}],"label","==",$area),"label",$metric,"bar","Sample comparison"),Svg([Rect(10,10,100,50,"primary"),SvgText(15,40,"Diagram","on-primary")],"0 0 160 80","Assumption diagram"),Flashcard("What changes?","The chosen assumptions."),Timer($seconds,"Practice timer"),HtmlView(${JSON.stringify(html)},"Canvas game",260),Map([MapMarker(51.5074,-0.1278,"London"),MapMarker(51.5033,-0.1195,"London Eye")],true,"Public itinerary"),SearchImage("London Eye","London landmark"),Popover("Details",[Text("Only declared inputs are saved.")]),Carousel([Text("Page one"),Text("Page two")],"Example pages")])
$units=5
$ready=false
$rate=3
$email=""
$notes=""
$metric="a"
$seconds=3
$area="All"`;
const controls = `DatePicker($date,"Review date"),RadioGroup($choice,"Review option",["First","Second"]),SegmentedControl($segment,"Review segment",["A","B"]),IconButton("Increment count",@Set($clicks,$clicks+1),"increment","Plus"),Pressable("Reset count",@Set($clicks,0),"reset-count",[Text("Local action")]),Text("Clicks: "+$clicks),Link("Open UI reference","https://github.com/thesysdev/open-intelligent-ui"),YouTube("jNQXAC9IVRw","Public video")`;
const completeProgram =
  program.replace(
    'Carousel([Text("Page one"),Text("Page two")],"Example pages")',
    `Carousel([Text("Page one"),Text("Page two")],"Example pages"),${controls}`,
  ) + '\n$date="2026-10-13"\n$choice="First"\n$segment="A"\n$clicks=0';
const parsed = validateProgram(completeProgram);
const block: UiBlock = {
  ...parsed,
  program: completeProgram,
  program_sha256: "a".repeat(64),
  fallback: "Adjust these assumptions and ask Nova to review them.",
  state_defaults: parsed.state,
  evidence: {},
  provenance: { evidence_ids: [], scope: "user_simulation", assumption: true },
};
let state = {
  revision: 0,
  state: parsed.state,
  program_sha256: block.program_sha256,
};
uiApi.get = async () => state;
uiApi.put = async (_, input) =>
  (state = { ...input, revision: input.revision + 1 });
uiApi.media = async (_, type) => {
  if (type === "SearchImage") throw new Error("Offline image fixture");
  return {
    markers: [
      { latitude: 51.5074, longitude: -0.1278, label: "London" },
      { latitude: 51.5033, longitude: -0.1195, label: "London Eye" },
    ],
    geometry: {
      type: "LineString",
      coordinates: [
        [-0.1278, 51.5074],
        [-0.1195, 51.5033],
      ],
    },
  };
};
Object.assign(window, {
  uiAcceptance: { flush: flushUiStates, saved: () => state },
});
const mode = new URLSearchParams(location.search).get("mode");
const agent: Agent = {
  agent_id: "qa",
  owner_name: "qa",
  name: "Nova acceptance",
  description: "Acceptance fixture",
  database_name: "sample",
  schema_name: null,
  avatar: null,
  color: null,
  model_provider_id: null,
  model_name: null,
  instructions_response: "",
  instructions_orchestration: "",
  response_style: null,
  sample_questions: [
    "Build an assumptions calculator",
    "Compare these example values",
  ],
  budget_seconds: 60,
  budget_tokens: null,
  tool_not_accessible: "",
  default_tools: [],
  default_skills: [],
  policy: "auto_read_only",
  semantic_model_id: null,
  semantic_model_ids: [],
  visibility: "private",
  created_at: "",
  updated_at: "",
};
const query = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});
if (mode) {
  useAuthStore.getState().auth.setAccessToken("acceptance-fixture");
  useAuthStore.getState().auth.setUser({
    username: "qa",
    roles: ["analyst"],
    activeRole: "analyst",
  });
  window.fetch = async (input) => {
    const path = String(input);
    const result = path.endsWith("/agents/studio/settings")
      ? { preferences: { extended_thinking: false } }
      : path.includes("/agents/qa/threads/qa")
        ? {
            thread_id: "qa",
            title: "Interactive answer",
            messages: [
              {
                role: "user",
                message_id: "qa-user",
                content: "Build an assumptions calculator",
                created_at: "2026-10-13T00:00:00Z",
              },
              {
                role: "assistant",
                message_id: "qa-answer",
                content: block.fallback,
                created_at: "2026-10-13T00:00:01Z",
                steps: [
                  {
                    kind: "ui",
                    content_id: "content-1",
                    content_index: 1,
                    ...block,
                  },
                ],
              },
            ],
          }
        : null;
    return new Response(
      JSON.stringify(result ?? { detail: "Fixture capability unavailable" }),
      {
        status: result ? 200 : 404,
        headers: { "Content-Type": "application/json" },
      },
    );
  };
  document.documentElement.style.height = document.body.style.height = "100%";
  document.body.style.margin = "0";
  document.body.style.overflow = "hidden";
  document.getElementById("root")!.style.height = "100%";
}
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={query}>
    <ThemeProvider defaultTheme={theme} storageKey="ui-acceptance-theme">
      {mode ? (
        <main className="flex h-full min-h-0 min-w-0 flex-col bg-background">
          <header className="shrink-0 border-b border-border px-4 py-3">
            Nova Studio
          </header>
          <StudioChat
            agent={agent}
            agents={[agent]}
            onSelectAgent={() => {}}
            currentRole="analyst"
            activeThreadId={mode === "chat" ? "qa" : null}
            onThreadChange={() => {}}
          />
        </main>
      ) : (
        <main className="mx-auto min-w-0 max-w-6xl p-3 sm:p-6">
          <IntelligentUi
            block={block}
            complete
            source={{
              agentId: "qa",
              threadId: "qa",
              messageId: "qa",
              contentId: "content-1",
            }}
            onAction={async () => {
              document.getElementById("action-result")!.textContent =
                "Sent through Nova";
            }}
          />
          <p id="action-result" role="status" />
        </main>
      )}
    </ThemeProvider>
  </QueryClientProvider>,
);
