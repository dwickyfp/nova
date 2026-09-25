import { describe, expect, it } from "vitest";
import { boundAppContext } from "./app-context";

describe("Nove app-context redaction", () => {
  it("scrubs quoted multiword credentials from every free-form context section", () => {
    const poisoned =
      'password="correct horse battery staple" followed by private text';
    const context = boundAppContext({
      version: 1,
      surface: { id: poisoned, route: poisoned, title: poisoned },
      entity: {
        type: poisoned,
        id: poisoned,
        name: poisoned,
        metadata: { label: poisoned, apiKey: "hidden" },
      },
      selection: {
        type: poisoned,
        ids: [poisoned],
        text: poisoned,
        metadata: { label: poisoned },
      },
      editor: {
        documentId: poisoned,
        language: poisoned,
        selectedText: poisoned,
      },
      execution: {
        type: poisoned,
        executionId: poisoned,
        errorCode: poisoned,
        errorMessage: poisoned,
        resultSchema: [{ name: poisoned, type: poisoned }],
      },
      view: {
        activeTab: poisoned,
        filters: { label: poisoned },
        search: poisoned,
        sort: poisoned,
      },
      domain: {
        database: poisoned,
        schema: poisoned,
        role: poisoned,
        semanticModel: poisoned,
        providerId: poisoned,
      },
      capabilities: ["surface.refresh", "token.leak"],
      events: [
        {
          id: poisoned,
          timestamp: poisoned,
          source: "surface",
          type: poisoned,
          surfaceId: poisoned,
          correlationId: poisoned,
          artifactId: poisoned,
          executionId: poisoned,
          payload: { label: poisoned, credentialNote: "hidden" },
        },
      ],
    });
    const rendered = JSON.stringify(context);
    expect(rendered).not.toContain("correct horse battery staple");
    expect(rendered).not.toContain("followed by private text");
    expect(rendered).not.toContain("hidden");
    expect(context.surface).toEqual({
      id: "[REDACTED]",
      route: "[REDACTED]",
      title: "[REDACTED]",
    });
    expect(context.view?.search).toBe("[REDACTED]");
    expect(context.capabilities).toEqual(["surface.refresh"]);
    expect(context.entity?.metadata).toEqual({ label: "[REDACTED]" });
    expect(context.events?.[0].payload).toEqual({ label: "[REDACTED]" });
  });

  it("checks the whole input before clipping and catches unlabelled credential formats", () => {
    const context = boundAppContext({
      version: 1,
      surface: { id: "workspace.sql", route: "/workspaces" },
      entity: { type: "query", name: "sk-abcdefghijklmnopqrstuvwxyz012345" },
      view: {
        search: `${"x".repeat(240)} password='long private phrase'`,
        filters: { location: "https://alice:unmarkedpass@example.test/data" },
      },
      capabilities: [],
    });
    expect(context.entity?.name).toBe("[REDACTED]");
    expect(context.view?.search).toBe("[REDACTED]");
    expect(context.view?.filters).toEqual({ location: "[REDACTED]" });
  });
});
