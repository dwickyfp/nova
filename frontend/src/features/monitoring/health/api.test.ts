import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FINDING_TONE,
  OVERALL_LABEL,
  OVERALL_TONE,
  VERDICT_TONE,
  fetchAlerts,
  fetchReadiness,
  firingAlerts,
  sortAlerts,
  sortFindings,
  verdictLabel,
  type Alert,
  type Finding,
} from "./api";

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

function alert(overrides: Partial<Alert>): Alert {
  return {
    id: "x",
    title: "title",
    category: "membership",
    severity: "warning",
    status: "ok",
    value: null,
    threshold: "t",
    description: "d",
    detail: "det",
    ...overrides,
  };
}

describe("monitoring health API", () => {
  it("fetches alerts from the monitoring endpoint", async () => {
    await fetchAlerts();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/monitoring/alerts");
    expect(fetchMock.mock.calls[0][1]?.method ?? "GET").toBe("GET");
  });

  it("fetches the readiness audit", async () => {
    await fetchReadiness();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/monitoring/readiness");
  });
});

describe("sortAlerts", () => {
  it("orders firing first, then unknown, then ok", () => {
    const sorted = sortAlerts([
      alert({ id: "ok", status: "ok" }),
      alert({ id: "unknown", status: "unknown" }),
      alert({ id: "firing", status: "firing" }),
    ]);
    expect(sorted.map((a) => a.id)).toEqual(["firing", "unknown", "ok"]);
  });

  it("orders critical before warning within firing", () => {
    const sorted = sortAlerts([
      alert({ id: "warn", status: "firing", severity: "warning" }),
      alert({ id: "crit", status: "firing", severity: "critical" }),
    ]);
    expect(sorted.map((a) => a.id)).toEqual(["crit", "warn"]);
  });

  it("is stable by category then title", () => {
    const sorted = sortAlerts([
      alert({ id: "b", category: "z", title: "b" }),
      alert({ id: "a", category: "a", title: "a" }),
    ]);
    expect(sorted.map((a) => a.id)).toEqual(["a", "b"]);
  });

  it("does not mutate the input", () => {
    const input = [
      alert({ id: "ok", status: "ok" }),
      alert({ id: "f", status: "firing" }),
    ];
    sortAlerts(input);
    expect(input[0].id).toBe("ok");
  });
});

describe("firingAlerts", () => {
  it("keeps only firing alerts", () => {
    const result = firingAlerts([
      alert({ id: "f", status: "firing" }),
      alert({ id: "u", status: "unknown" }),
      alert({ id: "o", status: "ok" }),
    ]);
    expect(result.map((a) => a.id)).toEqual(["f"]);
  });
});

describe("overall tone and label maps", () => {
  it("maps every overall state to a tone", () => {
    for (const state of [
      "healthy",
      "warning",
      "critical",
      "degraded",
      "unavailable",
    ] as const) {
      expect(OVERALL_TONE[state]).toBeTruthy();
      expect(OVERALL_LABEL[state]).toBeTruthy();
    }
  });

  it("matches the backend verdict strings", () => {
    expect(verdictLabel("PRODUCTION_ACCEPTANCE_PASSED")).toBe(
      "Production ready",
    );
    expect(verdictLabel("PRODUCTION_ACCEPTANCE_BLOCKED")).toBe("Blocked");
    expect(VERDICT_TONE.PRODUCTION_ACCEPTANCE_BLOCKED).toBe("danger");
  });
});

describe("sortFindings", () => {
  it("orders worst-first: BLOCKED, NEEDS_CHANGE, REVIEW, PASS", () => {
    const findings: Finding[] = [
      { category: "c", name: "pass", status: "PASS", detail: "" },
      { category: "c", name: "review", status: "REVIEW", detail: "" },
      { category: "c", name: "blocked", status: "BLOCKED", detail: "" },
      { category: "c", name: "change", status: "NEEDS_CHANGE", detail: "" },
    ];
    expect(sortFindings(findings).map((f) => f.status)).toEqual([
      "BLOCKED",
      "NEEDS_CHANGE",
      "REVIEW",
      "PASS",
    ]);
  });

  it("every finding status has a tone", () => {
    expect(FINDING_TONE.BLOCKED).toBe("danger");
    expect(FINDING_TONE.PASS).toBe("success");
    expect(FINDING_TONE.NEEDS_CHANGE).toBe("warning");
    expect(FINDING_TONE.REVIEW).toBe("neutral");
  });
});
