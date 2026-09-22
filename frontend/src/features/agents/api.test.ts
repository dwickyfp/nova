import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { agentsApi } from "./api";

/**
 * Locks the wire paths of the Agent Studio client (Phase 12). A path change here
 * without a matching backend change is a 404 in production, so the paths are
 * asserted rather than left to integration testing.
 */

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

describe("agents api paths", () => {
  it("lists models under the agent-scoped semantic endpoint", async () => {
    await agentsApi.listSemanticModels();
    expect(requestedUrl()).toBe("/api/v1/agents/semantic-models");
  });

  it("encodes the agent id in the threads path", async () => {
    await agentsApi.createThread("agent/with space");
    expect(requestedUrl()).toBe("/api/v1/agents/agent%2Fwith%20space/threads");
  });

  it("encodes both ids in the thread detail path", async () => {
    await agentsApi.getThread("a b", "c/d");
    expect(requestedUrl()).toBe("/api/v1/agents/a%20b/threads/c%2Fd");
  });

  it("posts a tool-call decision to the agent-scoped path", async () => {
    await agentsApi.decideToolCall("ag1", "tc1", "allow_session");
    expect(requestedUrl()).toBe("/api/v1/agents/ag1/tool-calls/tc1/decision");
  });

  it("validates a semantic model without saving", async () => {
    await agentsApi.validateSemanticModel("version: 0.1.1");
    expect(requestedUrl()).toBe("/api/v1/agents/semantic-models/validate");
  });

  it("previews a semantic question against an encoded model id", async () => {
    await agentsApi.previewSemanticQuestion("sales/model", "Revenue by region");
    expect(requestedUrl()).toBe(
      "/api/v1/agents/semantic-models/sales%2Fmodel/preview",
    );
  });

  it("loads semantic lint findings", async () => {
    await agentsApi.lintSemanticModel("sales model");
    expect(requestedUrl()).toBe(
      "/api/v1/agents/semantic-models/sales%20model/lint",
    );
  });

  it("stores a verified semantic plan and SQL together", async () => {
    await agentsApi.createVerifiedQuery("sales", {
      question: "Revenue by region",
      semantic_plan: { metrics: ["total_revenue"] },
      verified_sql: "SELECT 1",
    });
    expect(requestedUrl()).toBe(
      "/api/v1/agents/semantic-models/sales/verified-queries",
    );
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toMatchObject(
      {
        semantic_plan: { metrics: ["total_revenue"] },
        verified_sql: "SELECT 1",
      },
    );
  });
});
