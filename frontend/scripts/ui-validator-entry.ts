import { readFileSync } from "node:fs";
import { generateSystemPrompt } from "@openuidev/lang-core";
import {
  createSchemaLibrary,
  UI_CATALOG_VERSION,
  uiPromptRules,
  uiPromptExamples,
} from "../src/features/studio/intelligent-ui/catalog";
import { validateProgram } from "../src/features/studio/intelligent-ui/validator";

try {
  const input = JSON.parse(readFileSync(0, "utf8"));
  if (input.operation === "catalog") {
    const library = createSchemaLibrary();
    process.stdout.write(
      JSON.stringify({
        version: UI_CATALOG_VERSION,
        schema: library.toJSONSchema(),
        prompt: generateSystemPrompt({
          library: library.toSpec(),
          promptOptions: { bindings: true, additionalRules: uiPromptRules, examples: uiPromptExamples },
        }),
      }),
    );
  } else {
    process.stdout.write(
      JSON.stringify({
        ok: true,
        ...validateProgram(
          input.program,
          input.evidence,
          input.business,
          input.state,
          input.private_context,
        ),
      }),
    );
  }
} catch {
  process.stdout.write(
    JSON.stringify({
      ok: false,
      error: "Program or state failed Nova UI validation.",
    }),
  );
  process.exitCode = 1;
}
