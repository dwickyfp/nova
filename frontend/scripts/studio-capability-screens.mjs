// Screenshot recorded Studio turns for the capability benchmark.
//
//   NOVA_BENCH_UI=http://127.0.0.1:5173 NOVA_BENCH_USER=alice NOVA_BENCH_PASSWORD=... \
//     node scripts/studio-capability-screens.mjs <plan.json>
//
// The plan (written by `tests.benchmark.studio_capability.screens plan`) lists
// {id, agent_id, thread_id, file}. Each thread opens in Studio the way a person
// reopens it from History, signed in through the normal sign-in page, in the
// light theme at desktop width. The password is read from the environment and
// never printed.
import { chromium } from "playwright";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const planPath = process.argv[2];
if (!planPath) throw new Error("Usage: node scripts/studio-capability-screens.mjs <plan.json>");
const base = (process.env.NOVA_BENCH_UI ?? "http://127.0.0.1:5173").replace(/\/$/, "");
const username = process.env.NOVA_BENCH_USER ?? "alice";
const password = process.env.NOVA_BENCH_PASSWORD;
if (!password) throw new Error("Set NOVA_BENCH_PASSWORD.");
const plan = JSON.parse(readFileSync(planPath, "utf8"));
const settleMs = Number(process.env.NOVA_BENCH_SETTLE_MS ?? 3500);

const browser = await chromium.launch({ headless: true });
const results = [];
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    colorScheme: "light",
    reducedMotion: "reduce",
  });
  const host = new URL(base).hostname;
  await context.addCookies([{ name: "vite-ui-theme", value: "light", domain: host, path: "/" }]);
  const page = await context.newPage();
  await page.goto(`${base}/sign-in`);
  await page.getByPlaceholder("Enter your username").fill(username);
  await page.getByPlaceholder("********").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/sign-in"), { timeout: 30000 });

  for (const entry of plan) {
    const errors = [];
    const onError = (error) => errors.push(String(error.message).slice(0, 300));
    page.on("pageerror", onError);
    try {
      mkdirSync(dirname(entry.file), { recursive: true });
      await page.goto(`${base}/studio?agent=${encodeURIComponent(entry.agent_id)}&thread=${encodeURIComponent(entry.thread_id)}`);
      await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
      await page.waitForTimeout(settleMs);
      // Scroll every scrollable region to its end, so the last turn is in view.
      await page.evaluate(() => {
        for (const element of document.querySelectorAll("*")) {
          const style = getComputedStyle(element);
          if (/(auto|scroll)/.test(style.overflowY) && element.scrollHeight > element.clientHeight) {
            element.scrollTop = element.scrollHeight;
          }
        }
      });
      await page.waitForTimeout(800);
      await page.screenshot({ path: entry.file });
      results.push({ id: entry.id, file: entry.file, ok: true, page_errors: errors });
    } catch (error) {
      results.push({ id: entry.id, file: entry.file, ok: false, error: String(error).slice(0, 300) });
    } finally {
      page.off("pageerror", onError);
    }
  }
} finally {
  await browser.close();
}
writeFileSync(planPath.replace(/\.json$/, ".result.json"), JSON.stringify(results, null, 1));
console.log(`${results.filter((r) => r.ok).length}/${results.length} screenshots`);
