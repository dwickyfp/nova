import { afterEach, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AutoRun } from "@/features/agents/api";
import { ResourcePanel } from "./resource-panel";
import { workflowResourcesApi } from "./workflow-api";

afterEach(async () => { await cleanup(); vi.restoreAllMocks(); });

it("grants only explicitly selected metadata references to the chosen specialist", async () => {
  const runs = [{ run_id: "root", depth: 0 }, { run_id: "child", depth: 1, agent_path: "/root/analyst", agent_name: "Analyst" }] as AutoRun[];
  vi.spyOn(workflowResourcesApi, "list").mockResolvedValue({ resources: [
    { resource_id: "r1", message_id: "m1", name: "sales.csv", media_type: "text/plain", size_bytes: 64, digest: "digest1", attachment_index: 0 },
    { resource_id: "r2", message_id: "m1", name: "costs.csv", media_type: "text/plain", size_bytes: 64, digest: "digest2", attachment_index: 1 },
  ] });
  const grant = vi.spyOn(workflowResourcesApi, "grant").mockResolvedValue({ resource_refs: ["r1"], participant: "/root/analyst" });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ResourcePanel threadId="t1" runs={runs} /></QueryClientProvider>);
  await page.getByRole("button", { name: "Manage attachment access" }).click();
  await page.getByLabelText("sales.csv", { exact: true }).click();
  await page.getByLabelText("Specialist", { exact: true }).selectOptions("/root/analyst");
  await page.getByRole("button", { name: "Grant selected files" }).click();
  expect(grant).toHaveBeenCalledWith("t1", { root_run_id: "root", grantor: "/root", target: "/root/analyst", resource_refs: ["r1"] });
  await expect.element(page.getByText("Selected files are available to /root/analyst.")).toBeVisible();
  expect(JSON.stringify(grant.mock.calls)).not.toContain("content");
});
