import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchGraphRunsPage } from "./task-orchestration/api";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => ({}),
  });
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

function requestedUrl(index = 0) {
  return fetchMock.mock.calls[index][0] as string;
}

describe("task-orchestration paginated run history", () => {
  it("sends limit and offset as query parameters", async () => {
    await fetchGraphRunsPage("db1.default.g1", { limit: 25, offset: 50 });

    expect(requestedUrl()).toBe(
      "/api/v1/task-orchestration/graphs/db1.default.g1/runs?limit=25&offset=50",
    );
  });

  it("URL-encodes the graph id", async () => {
    await fetchGraphRunsPage("graph/with space", { limit: 10, offset: 0 });

    expect(requestedUrl()).toBe(
      "/api/v1/task-orchestration/graphs/graph%2Fwith%20space/runs?limit=10&offset=0",
    );
  });

  it("never targets the native /tasks surface", async () => {
    await fetchGraphRunsPage("g", { limit: 10, offset: 0 });

    expect(requestedUrl()).not.toMatch(/\/api\/v1\/tasks(\/|$)/);
  });
});
