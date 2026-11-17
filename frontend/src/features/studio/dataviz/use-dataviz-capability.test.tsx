import { act } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { useAuthStore } from "@/stores/auth-store";
import type { DatavizCapability, DatavizLocator } from "./api";
import { useDatavizCapability } from "./use-dataviz-capability";

const check = vi.hoisted(() => vi.fn());
vi.mock("./api", async (original) => ({
  ...await original<typeof import("./api")>(),
  datavizApi: { check },
}));

const clients: QueryClient[] = [];
const capability: DatavizCapability = {
  preview_ref: "test-reference", status: "SUPPORTED", reason_code: "OK",
  message: "", can_add: true, title: "Current result",
};
const locator = (id: string): DatavizLocator => ({
  thread_id: "thread", message_id: "message", content_id: id,
});

function Card({ id }: { id: string }) {
  const result = useDatavizCapability(locator(id));
  return <span>{result?.title ?? "Unavailable"}</span>;
}

async function mount(id: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  return render(<QueryClientProvider client={client}><Card id={id} /></QueryClientProvider>);
}

async function update(action: () => Promise<void>) {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  try { await act(action); }
  finally { vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", false); }
}

async function tick() {
  await update(async () => { await vi.advanceTimersByTimeAsync(31); });
}

beforeEach(() => {
  check.mockReset();
  check.mockResolvedValue({ results: [capability] });
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
});

afterEach(async () => {
  await cleanup();
  for (const client of clients.splice(0)) {
    await client.cancelQueries();
    client.clear();
  }
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("removes a queued capability when its last observer unmounts", async () => {
  const screen = await mount("closed");
  await screen.unmount();
  await tick();
  expect(check).not.toHaveBeenCalled();
});

it("keeps other queued cards when one is closed before the batch", async () => {
  const closed = await mount("closed");
  const open = await mount("open");
  await closed.unmount();
  await tick();
  expect(check).toHaveBeenCalledExactlyOnceWith([locator("open")], expect.any(AbortSignal));
  expect(open.container.textContent).toBe("Current result");
});

it("aborts a dispatched batch after every observer leaves", async () => {
  check.mockImplementation(() => new Promise(() => {}));
  const first = await mount("first");
  const second = await mount("second");
  await tick();
  const signal: AbortSignal = check.mock.calls[0][1];
  await first.unmount();
  expect(signal.aborted).toBe(false);
  await second.unmount();
  expect(signal.aborted).toBe(true);
});

it("preserves result positions when only one in-flight card is cancelled", async () => {
  let resolve!: (body: { results: DatavizCapability[] }) => void;
  check.mockImplementation(() => new Promise((done) => { resolve = done; }));
  const first = await mount("first");
  const second = await mount("second");
  await tick();
  await first.unmount();
  await update(async () => {
    resolve({ results: [{ ...capability, title: "Closed result" }, capability] });
  });
  await tick();
  expect(check.mock.calls[0][1].aborted).toBe(false);
  expect(second.container.textContent).toBe("Current result");
});

it("discards an old security context even if the transport ignores abort", async () => {
  let oldResult!: (body: { results: DatavizCapability[] }) => void;
  check.mockImplementationOnce(() => new Promise((resolve) => { oldResult = resolve; }));
  const screen = await mount("same-card");
  await tick();
  const oldSignal: AbortSignal = check.mock.calls[0][1];
  await update(async () => {
    useAuthStore.setState((state) => ({ securityEpoch: state.securityEpoch + 1 }));
  });
  expect(oldSignal.aborted).toBe(true);
  await tick();
  await update(async () => { oldResult({ results: [{ ...capability, title: "Old role result" }] }); });
  await tick();
  expect(check).toHaveBeenCalledTimes(2);
  expect(screen.container.textContent).toBe("Current result");
});
