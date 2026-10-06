import { describe, expect, it } from "vitest";
import type { AutoRun } from "@/features/agents/api";
import { SETTLE_READS, treeRefetchInterval } from "./smart-agent-tree";

const run = (depth: number, status: string) => ({ run_id: `${depth}-${status}`, depth, status }) as AutoRun;

describe("treeRefetchInterval", () => {
  it("reads every second while the answer streams or the root works", () => {
    expect(treeRefetchInterval([], true, 0)).toBe(1000);
    expect(treeRefetchInterval([run(0, "running"), run(1, "completed")], false, 0)).toBe(1000);
  });

  it("stops once the root and every participant have settled", () => {
    expect(treeRefetchInterval([run(0, "completed"), run(1, "completed")], false, 1)).toBe(false);
  });

  it("keeps reading for a while when a participant still shows as queued", () => {
    const runs = [run(0, "completed"), run(1, "queued")];
    expect(treeRefetchInterval(runs, false, 1)).toBe(3000);
    expect(treeRefetchInterval(runs, false, SETTLE_READS + 1)).toBe(false);
  });
});
