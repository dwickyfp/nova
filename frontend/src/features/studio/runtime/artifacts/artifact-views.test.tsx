import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import type { ArtifactPart, SaveSuggestionPart } from "../turn-reducer";
import { artifactsApi } from "./api";
import { SaveSuggestion } from "./artifact-views";
import { artifactScope } from "./scope";
import type { ArtifactSaved } from "./types";

/**
 * The save suggestion: the person decides. It opens the dashboard dialog and
 * never saves on its own; a role that cannot author sees why instead.
 */

const opened = vi.fn();
vi.mock("./scope", async (original) => ({
  ...(await original<typeof import("./scope")>()),
  useOpenDashboard: () => opened,
}));
vi.mock("../../dataviz/add-to-dataviz", async (original) => ({
  ...(await original<typeof import("../../dataviz/add-to-dataviz")>()),
  AddToDatavizDialog: ({ draft }: { draft: { title: string } }) => (
    <div role="dialog" aria-label="Save to dashboard">Pick a dashboard for {draft.title}</div>
  ),
}));

const REF = "dr1.a.b";
const artifact: ArtifactPart = {
  id: "run#3",
  seq: 3,
  state: "done",
  type: "artifact_preview",
  draft: {
    draft_ref: REF,
    kind: "dataviz_chart",
    title: "Units by brand",
    revision: 1,
    chart: {} as ArtifactPart["draft"]["chart"],
  },
};
const saved: ArtifactSaved = {
  operation_id: "op",
  dashboard_id: "d1",
  dashboard_title: "Brand review",
  workspace_id: "w1",
  chart_id: "c1",
  widget_id: "wd1",
  revision: 2,
  draft_revision: 1,
};

function suggestion(extra: Partial<SaveSuggestionPart> = {}): SaveSuggestionPart {
  return {
    id: "run#4",
    seq: 4,
    state: "done",
    type: "save_suggestion",
    draftRefs: [REF],
    capability: "can_save",
    reasonCode: "OK",
    message: "Save this chart to a Dataviz dashboard to keep it.",
    saved: {},
    ...extra,
  };
}

const scope = (parts: { type: string }[] = [artifact]) =>
  artifactScope(parts, { ready: true, open: vi.fn(), apply: vi.fn() });

describe("save suggestion", () => {
  it("opens the dashboard dialog and saves nothing until the person confirms", async () => {
    const publish = vi.spyOn(artifactsApi, "publish");
    const screen = await render(<SaveSuggestion part={suggestion()} scope={scope()} />);
    expect(screen.container.textContent).toContain("Save this chart to a Dataviz dashboard to keep it.");
    expect(screen.getByRole("dialog").query()).toBeNull();
    await screen.getByRole("button", { name: "Save to dashboard…" }).click();
    await expect.element(screen.getByRole("dialog", { name: "Save to dashboard" })).toBeVisible();
    expect(publish).not.toHaveBeenCalled();
  });

  it("says why a role cannot save, with no save action", async () => {
    const message = "Your active role VIEWER cannot read the data behind this chart, so it cannot be saved to a dashboard.";
    const screen = await render(
      <SaveSuggestion part={suggestion({ capability: "role_cannot_author", reasonCode: "ROLE_CANNOT_READ_SOURCE", message })} scope={scope()} />,
    );
    await expect.element(screen.getByRole("note")).toHaveTextContent(message);
    expect(screen.getByRole("button").query()).toBeNull();
  });

  it("after a save shows where it went and never suggests that chart again", async () => {
    const screen = await render(<SaveSuggestion part={suggestion({ saved: { [REF]: saved } })} scope={scope()} />);
    expect(screen.container.textContent).toContain("Saved in Brand review");
    expect(screen.getByRole("button", { name: "Save to dashboard…" }).query()).toBeNull();
    await screen.getByRole("button", { name: "Open" }).click();
    expect(opened).toHaveBeenCalledWith(saved);
  });

  it("shows nothing for drafts this turn did not announce", async () => {
    const screen = await render(<SaveSuggestion part={suggestion()} scope={scope([])} />);
    expect(screen.container.textContent).toBe("");
  });
});
