import { describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { boundAppContext } from "./app-context";
import { defineNoveCapability, NoveApplicationEvents, NoveSurfaceRegistry } from "./surface-registry";

describe("Nove application context", () => {
  it("bounds event evidence, redacts secrets, and keeps the active object", () => {
    const context = boundAppContext({
      version: 1,
      surface: { id: "workspace.sql", route: "/workspaces", title: "SQL" },
      entity: { type: "query", id: "q-1", metadata: { rowCount: 2, password: "private" } },
      selection: { text: "CREATE USER alice IDENTIFIED BY 'private'" },
      capabilities: Array.from({ length: 40 }, (_, index) => `action.${index}`),
      events: [{
        id: "e-1", timestamp: new Date().toISOString(), source: "execution", type: "query_failed",
        surfaceId: "workspace.sql",
        payload: { sql: "SELECT * FROM missing_table", error: "table not found", token: "private" },
      }],
    });
    expect(context.entity?.id).toBe("q-1");
    expect(context.entity?.metadata).toEqual({ rowCount: 2 });
    expect(context.selection?.text).toBe("[REDACTED]");
    expect(context.capabilities).toHaveLength(32);
    expect(context.events?.[0].payload).toEqual({ sql: "SELECT * FROM missing_table", error: "table not found" });
    expect(new TextEncoder().encode(JSON.stringify(context)).length).toBeLessThanOrEqual(16_384);
  });

  it("does not forward credential-shaped SQL from an execution event", () => {
    const context = boundAppContext({
      version: 1, surface: { id: "workspace.sql", route: "/workspaces" }, capabilities: [],
      events: [{
        id: "e-1", timestamp: new Date().toISOString(), source: "execution", type: "query_failed",
        payload: { sql: "CREATE USER alice IDENTIFIED BY 'private'" },
      }],
    });
    expect(context.events?.[0].payload).toBeUndefined();
  });
});

describe("Nove surface registry", () => {
  it("uses the mounted page and revokes stale capabilities on navigation", async () => {
    const registry = new NoveSurfaceRegistry();
    const changed = vi.fn();
    const stopListening = registry.subscribe(changed);
    const roleAction = vi.fn();
    const unregisterRole = registry.register(() => ({
      id: "roles.detail", route: "/roles/ANALYST", title: "Role ANALYST",
      context: () => ({ entity: { type: "role", id: "ANALYST" } }),
      capabilities: [defineNoveCapability({
        name: "surface.refresh", risk: "safe", argsSchema: z.object({}), execute: roleAction,
      })],
    }));
    expect(registry.buildContext({}, []).entity?.id).toBe("ANALYST");
    const unregisterHistory = registry.register(() => ({
      id: "monitoring.query_history", route: "/query-history", context: () => ({ view: { filters: { status: "ERROR" } } }),
      capabilities: [],
    }));
    expect(registry.buildContext({}, []).surface.id).toBe("monitoring.query_history");
    await expect(registry.executeAction({ capability: "surface.refresh", args: {}, surfaceId: "roles.detail" })).rejects.toThrow();
    unregisterHistory();
    await registry.executeAction({ capability: "surface.refresh", args: {}, surfaceId: "roles.detail" });
    expect(roleAction).toHaveBeenCalledOnce();
    unregisterRole();
    expect(registry.buildContext({}, []).surface.id).toBe("nova.global");
    expect(changed).toHaveBeenCalled();
    stopListening();
  });

  it("rejects malformed and privileged client actions before executing", async () => {
    const registry = new NoveSurfaceRegistry();
    const mutate = vi.fn();
    registry.register(() => ({
      id: "roles.detail", route: "/roles/ANALYST", context: () => ({}),
      capabilities: [
        defineNoveCapability({
          name: "surface.set_filter", risk: "safe",
          argsSchema: z.object({ filter: z.literal("scope"), value: z.string().max(10) }),
          execute: mutate,
        }),
        defineNoveCapability({
          name: "grant.role", risk: "privileged", argsSchema: z.object({}), execute: mutate,
        }),
      ],
    }));
    await expect(registry.executeAction({ capability: "surface.set_filter", args: { filter: "bad", value: "x" } })).rejects.toThrow();
    await expect(registry.executeAction({ capability: "grant.role", args: {} })).rejects.toThrow("approval workflow");
    expect(mutate).not.toHaveBeenCalled();
  });

  it("allows declared navigation to replace the page while rejecting an unexpected switch", async () => {
    const registry = new NoveSurfaceRegistry();
    registry.register(() => ({
      id: "roles.list", route: "/roles", context: () => ({}),
      capabilities: [defineNoveCapability({
        name: "surface.select", risk: "safe", mayChangeSurface: true,
        argsSchema: z.object({ id: z.literal("ANALYST") }),
        execute: () => {
          registry.register(() => ({ id: "roles.detail", route: "/roles/ANALYST", context: () => ({ entity: { type: "role", id: "ANALYST" } }) }));
        },
      })],
    }));
    await expect(registry.executeAction({ capability: "surface.select", args: { id: "ANALYST" }, surfaceId: "roles.list" })).resolves.toBeUndefined();
    expect(registry.buildContext({}, []).surface.id).toBe("roles.detail");

    const strictRegistry = new NoveSurfaceRegistry();
    strictRegistry.register(() => ({
      id: "monitoring.query_history", route: "/query-history", context: () => ({}),
      capabilities: [defineNoveCapability({
        name: "surface.refresh", risk: "safe", argsSchema: z.object({}),
        execute: () => {
          strictRegistry.register(() => ({ id: "roles.detail", route: "/roles/ANALYST", context: () => ({}) }));
        },
      })],
    }));
    await expect(strictRegistry.executeAction({ capability: "surface.refresh", args: {}, surfaceId: "monitoring.query_history" })).rejects.toThrow("page changed");
  });

  it("keeps only bounded same-page events in turn context", () => {
    const registry = new NoveSurfaceRegistry();
    const events = new NoveApplicationEvents();
    registry.register(() => ({ id: "roles.detail", route: "/roles/ANALYST", context: () => ({}) }));
    for (let index = 0; index < 20; index += 1) {
      events.publish({ source: "surface", type: "entity_updated", surfaceId: index % 2 ? "roles.detail" : "workspace.sql", payload: { entityId: "ANALYST" } });
    }
    const context = registry.buildContext({}, events.getRecent());
    expect(context.events?.length).toBeLessThanOrEqual(8);
    expect(context.events?.every((event) => event.surfaceId === "roles.detail")).toBe(true);
  });
});
