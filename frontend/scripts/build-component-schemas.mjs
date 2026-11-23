// Export the Studio component registry (Zod) as JSON Schema for the backend.
// `--check` fails when the committed file differs: frontend and backend
// schemas cannot drift apart.
import { build } from "esbuild";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const target = fileURLToPath(
  new URL("../../backend/app/modules/assistant/ui_components.json", import.meta.url),
);
const result = await build({
  entryPoints: [
    fileURLToPath(new URL("../src/features/studio/runtime/components/schemas.ts", import.meta.url)),
  ],
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  write: false,
});
const source = result.outputFiles[0].text;
const module = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const text = `${JSON.stringify(module.componentContract(), null, 2)}\n`;
if (process.argv.includes("--check")) {
  if (readFileSync(target, "utf8") !== text)
    throw new Error("Studio component schema drift. Run pnpm components:bundle.");
} else writeFileSync(target, text);
