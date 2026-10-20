import { build, preview } from "vite";
import { chromium } from "playwright";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { resolve } from "node:path";

const output = resolve(process.env.NOVA_MAP_BENCHMARK_OUTPUT ?? "/tmp/nova-mapcn-benchmark");
mkdirSync(output, { recursive: true });
const fixture = resolve(".nova-map-benchmark.html");
const report = {};
const browser = await chromium.launch({ headless: true });
try {
  for (const renderer of ["legacy", "mapcn"]) {
    writeFileSync(fixture, '<!doctype html><html><head><title>Nova map benchmark</title></head><body><div id="root"></div><script type="module" src="/src/test-utils/intelligent-ui-preview.tsx"></script></body></html>');
    const outDir = `${output}/${renderer}`;
    try {
      await build({
        define: { "import.meta.env.VITE_STUDIO_MAPCN_ENABLED": JSON.stringify(renderer === "mapcn" ? "true" : "false") },
        build: { outDir, emptyOutDir: true, rolldownOptions: { input: fixture } },
      });
    } finally { rmSync(fixture, { force: true }); }
    const server = await preview({ build: { outDir }, preview: { port: 0, host: "127.0.0.1" } });
    const ready = [], interaction = [];
    try {
      for (let trial = 0; trial < 10; trial++) {
        const page = await browser.newPage({ viewport: { width: 768, height: 900 }, reducedMotion: "reduce" });
        await page.route("**/*", async route => {
          if (new URL(route.request().url()).hostname !== "127.0.0.1") throw new Error("Business map attempted an external resource");
          await route.continue();
        });
        const began = performance.now();
        await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/.nova-map-benchmark.html?component=geo&catalog=v2`);
        await page.locator('[aria-label="Map viewport"][aria-busy="false"]').waitFor();
        ready.push(performance.now() - began);
        await page.getByText("View locations", { exact: true }).click();
        const selected = performance.now();
        await page.getByRole("button", { name: "Select Bandung", exact: true }).click();
        await page.getByText("Selected: Bandung", { exact: true }).waitFor();
        interaction.push(performance.now() - selected);
        await page.close();
      }
    } finally { await new Promise((resolve, reject) => server.httpServer.close(e => e ? reject(e) : resolve())); }
    const summary = values => {
      const sorted = [...values].sort((a, b) => a - b);
      return { samples_ms: values, median_ms: (sorted[4] + sorted[5]) / 2, p95_ms: sorted[9] };
    };
    report[renderer] = { ready: summary(ready), selection: summary(interaction) };
  }
  writeFileSync(`${output}/report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally { rmSync(fixture, { force: true }); await browser.close(); }
