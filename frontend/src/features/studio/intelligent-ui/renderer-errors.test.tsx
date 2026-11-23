import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { useEffect } from "react";
import type { OpenUIError, RendererProps } from "@openuidev/react-lang";
import { IntelligentUi } from "./renderer";
import { validateProgram } from "./validator";
import type { UiBlock } from "./types";

/**
 * How the OpenUI runtime's error reports reach the reader.
 *
 * The runtime's onError carries both errors and warnings; a warning means the
 * program still rendered. The runtime is replaced here so each report can be
 * sent exactly, independent of which programs happen to trigger it.
 */

const reported = vi.hoisted(() => ({ errors: [] as OpenUIError[] }));
vi.mock("@openuidev/react-lang", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@openuidev/react-lang")>();
  function Renderer({ onError }: RendererProps) {
    useEffect(() => {
      onError?.(reported.errors);
    }, [onError]);
    return <p>Rendered interactive answer</p>;
  }
  return { ...actual, Renderer };
});

function block(): UiBlock {
  const program = 'root = Col([Text("Adjust assumptions")])';
  const result = validateProgram(program);
  return {
    catalog_version: result.catalog_version,
    program_sha256: "a".repeat(64),
    program,
    fallback: "The fallback answer.",
    state_defaults: result.state,
    actions: result.actions,
    evidence: {},
    provenance: { evidence_ids: [], scope: "user_simulation", assumption: true },
  };
}

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
});

const warning: OpenUIError = {
  source: "parser",
  code: "no-root",
  message: "No root statement. Rendering the first statement that calls the root component Col",
  severity: "warning",
};
const failure: OpenUIError = {
  source: "runtime",
  code: "render-error",
  message: "Component failed to render",
};

describe("interactive answer error reports", () => {
  it("keeps rendering when the runtime reports only warnings", async () => {
    reported.errors = [warning];
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const screen = await render(<IntelligentUi block={block()} complete />);
    await expect.element(screen.getByText("Rendered interactive answer")).toBeVisible();
    await expect.element(screen.getByText("Interactive answer unavailable.")).not.toBeInTheDocument();
  });

  it("shows the fallback when an error accompanies a warning", async () => {
    reported.errors = [warning, failure];
    const screen = await render(<IntelligentUi block={block()} complete />);
    await expect.element(screen.getByText("Interactive answer unavailable.")).toBeVisible();
    await expect.element(screen.getByText("The fallback answer.")).toBeVisible();
  });
});
