import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const out = fileURLToPath(
  new URL("../../backend/app/modules/assistant/ui_runtime/", import.meta.url),
);
mkdirSync(out, { recursive: true });
const result = await build({
  entryPoints: [
    fileURLToPath(new URL("./ui-validator-entry.ts", import.meta.url)),
  ],
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node22",
  minify: true,
  write: false,
  legalComments: "inline",
});
const bundle = result.outputFiles[0].text;
const path = `${out}validator.cjs`;
const notice =
  ["@openuidev/lang-core", "zod", "acorn"]
    .map((name) => {
      const root = new URL(`../node_modules/${name}/`, import.meta.url);
      const metadata = JSON.parse(
        readFileSync(new URL("package.json", root), "utf8"),
      );
      return `${name} ${metadata.version}\n\n${readFileSync(new URL("LICENSE", root), "utf8")}\n`;
    })
    .join("\n")
    .trimEnd() + "\n";
if (process.argv.includes("--check")) {
  if (readFileSync(path, "utf8") !== bundle)
    throw new Error("UI validator bundle drift. Run pnpm ui:bundle.");
  if (readFileSync(`${out}NOTICE.txt`, "utf8") !== notice)
    throw new Error("UI runtime license drift.");
} else {
  writeFileSync(path, bundle);
  writeFileSync(`${out}NOTICE.txt`, notice);
}
const catalog = JSON.parse(
  execFileSync(process.execPath, [path], {
    input: JSON.stringify({ operation: "catalog" }),
    maxBuffer: 2000000,
  }),
);
catalog.bundle_sha256 = createHash("sha256").update(bundle).digest("hex");
const catalogText = `${JSON.stringify(catalog, null, 2)}\n`;
if (process.argv.includes("--check")) {
  if (readFileSync(`${out}catalog.json`, "utf8") !== catalogText)
    throw new Error("UI schema/prompt drift.");
} else writeFileSync(`${out}catalog.json`, catalogText);
