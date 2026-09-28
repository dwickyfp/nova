import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { page, userEvent } from "vitest/browser";
import { afterEach, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { sharingApi, type Share } from "@/features/agents/studio-intelligence-api";
import { ShareDialog } from "./share-dialog";
import "@/styles/index.css";

const existing: Share = {
  share_id: "s1", object_type: "thread", object_id: "t1", owner_name: "alice",
  target_type: "role", target_name: "SALES_ANALYST",
};

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ShareDialog objectType="thread" objectId="t1" title="Revenue review" onClose={() => {}} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

it("shares with a user and lists who already has access", async () => {
  vi.spyOn(sharingApi, "list").mockResolvedValue({ shares: [existing], count: 1 });
  const create = vi.spyOn(sharingApi, "create").mockResolvedValue(existing);
  mount();
  await expect.element(page.getByText("SALES_ANALYST")).toBeVisible();
  const share = page.getByRole("button", { name: "Share", exact: true });
  await expect.element(share).toBeDisabled();
  await page.getByLabelText("Username").fill("bob");
  await share.click();
  expect(create).toHaveBeenCalledWith("thread", "t1", { target_type: "user", target_name: "bob" });
});

it("rejects a name that is not a StarRocks identifier", async () => {
  vi.spyOn(sharingApi, "list").mockResolvedValue({ shares: [], count: 0 });
  mount();
  await page.getByLabelText("Username").fill("bob; drop");
  await userEvent.keyboard("{Enter}");
  await expect.element(page.getByRole("button", { name: "Share", exact: true })).toBeDisabled();
  await expect.element(page.getByText("Only you.")).toBeVisible();
});
