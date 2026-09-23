import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createStarterTemplateFile,
  starterTemplates,
} from "./starter-templates";
import type { WorkspaceEntry } from "./types";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: mocks }));

describe("starter template worksheets", () => {
  beforeEach(() => {
    mocks.post.mockReset();
    mocks.post.mockImplementation(
      async (_path: string, body: { name: string; content: string }) => ({
        entry: { id: "created", name: body.name },
        content: body.content,
      }),
    );
  });

  it("saves each template's SQL as the new worksheet content", async () => {
    for (const template of starterTemplates) {
      const file = await createStarterTemplateFile(template, []);
      expect(mocks.post).toHaveBeenLastCalledWith("/workspaces/files", {
        name: `${template.title}.sql`,
        parent_path: "",
        content: template.sql,
      });
      expect(file.content).toBe(template.sql);
      expect(file.content.trim()).not.toBe("");
    }
  });

  it("uses a new filename when the template was opened before", async () => {
    const template = starterTemplates[0];
    const entries: WorkspaceEntry[] = [
      {
        id: "first",
        name: `${template.title}.sql`,
        parent_path: "",
        path: "",
        entry_type: "file",
        size_bytes: 1,
      },
      {
        id: "second",
        name: `${template.title}-2.sql`,
        parent_path: "",
        path: "",
        entry_type: "file",
        size_bytes: 1,
      },
    ];
    await createStarterTemplateFile(template, entries);
    expect(mocks.post).toHaveBeenCalledWith(
      "/workspaces/files",
      expect.objectContaining({
        name: `${template.title}-3.sql`,
      }),
    );
  });

  it("uses the bundled Nova examples instead of generic table placeholders", () => {
    for (const template of starterTemplates.filter(
      (item) => item.id !== "query-stage",
    )) {
      expect(template.sql).toMatch(/NOVA_(DEMO|CATALOG|ANALYTICS)\./);
      expect(template.sql).not.toMatch(
        /your_table|source_table|target_table|text_column/,
      );
    }
  });

  it("keeps Nova DDL at the start of each statement", () => {
    expect(
      starterTemplates.find((item) => item.id === "demo-order-anomalies")?.sql,
    ).toMatch(/^CREATE ML_MODEL\b/);
    expect(
      starterTemplates.find((item) => item.id === "daily-sales-task")?.sql,
    ).toMatch(/;\s+CREATE TASK\b/);
  });
});
