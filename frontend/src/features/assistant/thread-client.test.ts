import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetGrant, setGrant } from "./thread-client";

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: {
    getState: () => ({ auth: { accessToken: "test-token", reset: vi.fn() } }),
  },
}));

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockClear();
  fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("resetGrant", () => {
  it("issues DELETE against the thread grant route and resolves on 204", async () => {
    await expect(resetGrant("thread-1")).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/assistant/threads/thread-1/grant");
    expect(init.method).toBe("DELETE");
    expect(init.body).toBeUndefined();
  });

  it("url-encodes the thread id", async () => {
    await resetGrant("thread/with space");

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/assistant/threads/thread%2Fwith%20space/grant");
  });

  it("treats a 404 as an idempotent revoke instead of a failure", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Thread not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(resetGrant("thread-1")).resolves.toBeUndefined();
  });

  it("rejects with the backend detail when the revoke fails for another reason", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Thread is locked" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(resetGrant("thread-1")).rejects.toThrow("Thread is locked");
  });

  it("rejects on a network failure instead of swallowing it", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(resetGrant("thread-1")).rejects.toThrow("Failed to fetch");
  });
});

describe("setGrant", () => {
  it("PUTs the read-only flag and returns the backend's grant state", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ grant_active: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(setGrant("thread-1", true)).resolves.toBe(true);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/assistant/threads/thread-1/grant");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ always_allow_read_only: true });
  });

  it("url-encodes the thread id", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ grant_active: false }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await setGrant("thread/with space", false);

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/assistant/threads/thread%2Fwith%20space/grant");
  });

  it("reports false on a 404 instead of throwing", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Thread not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(setGrant("thread-1", true)).resolves.toBe(false);
  });

  it("rejects with the backend detail on any other failure", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Not allowed" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(setGrant("thread-1", true)).rejects.toThrow("Not allowed");
  });
});
