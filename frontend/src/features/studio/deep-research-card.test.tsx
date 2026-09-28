import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { page } from "vitest/browser";
import { afterEach, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { deepResearchApi, type DeepResearchRun } from "@/features/agents/studio-intelligence-api";
import { DeepResearchCard } from "./deep-research-card";
import "@/styles/index.css";

const running: DeepResearchRun = {
  run_id: "r1", agent_id: "sales", thread_id: "t1",
  question: "Kenapa penjualan Surabaya turun kuartal ini?",
  status: "running",
  plan: ["Penjualan per kategori", "Penjualan per kanal"],
  progress: [
    { question: "Penjualan per kategori", status: "done" },
    { question: "Penjualan per kanal", status: "running" },
  ],
  report: null,
};

function mount(props: { onOpenReport?: (id: string) => void; onDismiss?: () => void } = {}) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <div className="w-full max-w-3xl bg-background p-4">
        <DeepResearchCard agentId="sales" runId="r1"
          onOpenReport={props.onOpenReport ?? (() => {})} onDismiss={props.onDismiss ?? (() => {})} />
      </div>
    </QueryClientProvider>,
  );
}

afterEach(async () => {
  vi.restoreAllMocks();
  await page.viewport(1440, 900);
});

it("shows each investigation and cancels a running run", async () => {
  vi.spyOn(deepResearchApi, "get").mockResolvedValue(running);
  const cancel = vi.spyOn(deepResearchApi, "cancel").mockResolvedValue({ cancelling: true });
  mount();
  await expect.element(page.getByText("Running investigations")).toBeVisible();
  await expect.element(page.getByText("Penjualan per kanal")).toBeVisible();
  await expect.element(page.getByLabelText("Done")).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  expect(cancel).toHaveBeenCalledWith("sales", "r1");
});

it("opens the report thread when the run is done", async () => {
  vi.spyOn(deepResearchApi, "get").mockResolvedValue({
    ...running, status: "done",
    progress: running.progress!.map((item) => ({ ...item, status: "done" })),
  });
  const open = vi.fn();
  mount({ onOpenReport: open });
  await page.getByRole("button", { name: "Open the report" }).click();
  expect(open).toHaveBeenCalledWith("t1");
  await expect.element(page.getByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
});

it("fits a phone width without horizontal scroll", async () => {
  await page.viewport(360, 740);
  vi.spyOn(deepResearchApi, "get").mockResolvedValue({
    ...running, question: "x".repeat(300),
  });
  mount();
  await expect.element(page.getByText("Running investigations")).toBeVisible();
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(360);
});
