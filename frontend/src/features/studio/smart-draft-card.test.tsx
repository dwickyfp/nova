import { afterEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { SmartDraft } from "@/features/assistant/types";
import { ontologyApi } from "@/features/ontology/api";
import { newspaperApi } from "./news/newspaper-api";
import { replayThread } from "./studio-transcript";
import { SmartDraftCard } from "./smart-draft-card";
import "@/styles/index.css";

const PROPOSAL: SmartDraft = {
  draft_kind: "knowledge_proposal",
  title: "Add other names to Finance.NetRevenue",
  detail: "Other names: revenue bersih",
  payload: { title: "Add other names to Finance.NetRevenue", rationale: "r", changes: [] },
};
const FINDING: SmartDraft = {
  draft_kind: "investigation_update",
  title: "Finding for the incident review",
  detail: "Refunds rose in Jakarta",
  incident_id: "inc_1",
  payload: { draft: "signed.draft" },
};

function show(draft: SmartDraft) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><SmartDraftCard draft={draft} /></QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

describe("SmartDraftCard", () => {
  it("files a definition change only when the user submits it", async () => {
    const propose = vi.spyOn(ontologyApi, "propose").mockResolvedValue({} as never);
    await show(PROPOSAL);
    const card = page.getByRole("region", { name: "Proposed definition change" });
    await expect.element(card).toHaveTextContent("Other names: revenue bersih");
    await expect.element(card).toHaveTextContent("Nothing is filed yet.");
    expect(propose).not.toHaveBeenCalled();
    await page.getByRole("button", { name: "Submit for review" }).click();
    await expect.element(card).toHaveTextContent("Submitted.");
    const sent = propose.mock.calls[0][0];
    expect(sent).toMatchObject(PROPOSAL.payload);
    expect(sent.operation_id).toMatch(/^draft-[0-9a-f]{40}$/);
    await expect.element(page.getByRole("button", { name: "Submit for review" })).not.toBeInTheDocument();
  });

  it("posts a finding with its signed draft and keeps the button after a failure", async () => {
    const submit = vi.spyOn(newspaperApi, "submitFinding")
      .mockRejectedValueOnce(new Error("This draft can no longer be submitted."))
      .mockResolvedValue({ update_id: "upd_1", status: "PENDING_REVIEW" });
    await show(FINDING);
    const card = page.getByRole("region", { name: "Proposed investigation finding" });
    await page.getByRole("button", { name: "Post for review" }).click();
    await expect.poll(() => submit.mock.calls.length).toBe(1);
    await expect.element(page.getByRole("button", { name: "Post for review" })).toBeEnabled();
    await expect.element(card).toHaveTextContent("Nothing is posted yet.");
    await page.getByRole("button", { name: "Post for review" }).click();
    await expect.element(card).toHaveTextContent("Posted for review.");
    expect(submit).toHaveBeenLastCalledWith("inc_1", "signed.draft");
  });

  it("can be set aside without filing anything", async () => {
    const propose = vi.spyOn(ontologyApi, "propose");
    await show(PROPOSAL);
    await page.getByRole("button", { name: "Not now" }).click();
    await expect.element(page.getByRole("region", { name: "Proposed definition change" })).not.toBeInTheDocument();
    expect(propose).not.toHaveBeenCalled();
  });

  it("comes back with the stored Smart answer", () => {
    const turns = replayThread([
      { message_id: "q", role: "user", content: "Record revenue bersih", created_at: "2026-11-06T10:00:00Z", steps: [] },
      { message_id: "a", role: "assistant", content: "Ready to submit.", created_at: "2026-11-06T10:00:05Z",
        steps: [{ kind: "smart_draft", ...PROPOSAL }, { kind: "smart_draft", draft_kind: "bogus" } as never] },
    ] as never);
    expect(turns[turns.length - 1]?.smartDrafts).toEqual([PROPOSAL]);
  });
});
