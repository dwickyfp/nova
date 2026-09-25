import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const backendDir = path.resolve(frontendDir, "../backend");
const origin = process.env.NOVA_UI_URL ?? "http://127.0.0.1:5173";
const question = process.env.NOVA_STUDIO_QUESTION ??
  "Bandingkan recognized revenue, gross margin, dan order count per channel untuk 2025.";
const answerCue = process.env.NOVA_STUDIO_ANSWER_CUE ?? "WhatsApp B2B";
const expectedChildAgentId = process.env.NOVA_STUDIO_CHILD_AGENT_ID ?? "e84f36c1-ff55-4387-a895-342b3269b4a8";
const expectAnswerTable = process.env.NOVA_STUDIO_EXPECT_TABLE !== "0";
const steerText = process.env.NOVA_STUDIO_STEER ?? "Please show a side-by-side table for all five channels.";
const outputDir = path.resolve(
  process.env.NOVA_STUDIO_QA_OUTPUT ??
    path.join(frontendDir, "__screenshots__", `studio-harness-${Date.now()}`),
);
const terminalStatuses = new Set(["completed", "failed", "cancelled", "interrupted"]);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function activeAdminToken() {
  if (process.env.NOVA_STUDIO_TOKEN) return process.env.NOVA_STUDIO_TOKEN;
  const script = `
import asyncio
from app.core.redis import SESSION_PREFIX, session_store
from app.core.security import create_access_token

async def main():
    await session_store.init()
    try:
        redis = session_store._redis
        assert redis is not None
        candidates = []
        async for key in redis.scan_iter(match=f"{SESSION_PREFIX}*"):
            username, role, ttl = await asyncio.gather(
                redis.hget(key, "username"),
                redis.hget(key, "active_role"),
                redis.ttl(key),
            )
            if username == "nova_admin" and role == "ACCOUNTADMIN" and ttl > 0:
                candidates.append((ttl, key.removeprefix(SESSION_PREFIX)))
        if not candidates:
            raise RuntimeError("No active nova_admin ACCOUNTADMIN session")
        print(create_access_token("nova_admin", max(candidates)[1]))
    finally:
        await session_store.close()

asyncio.run(main())
`;
  return execFileSync(path.join(backendDir, ".venv/bin/python"), ["-c", script], {
    cwd: backendDir,
    encoding: "utf8",
    env: process.env,
  }).trim();
}

async function signIn(context, page) {
  if (process.env.NOVA_STUDIO_PASSWORD) {
    await page.goto(`${origin}/sign-in?redirect=%2Fstudio`);
    await page.getByRole("textbox", { name: /username/i }).fill("nova_admin");
    await page.getByLabel("Password", { exact: true }).fill(process.env.NOVA_STUDIO_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL((url) => url.pathname === "/studio", { timeout: 20000 });
    const cookie = (await context.cookies(origin)).find((item) => item.name === "nova_access_token");
    assert(cookie?.value, "Login succeeded without an access-token cookie");
    return { token: cookie.value, mode: "password login" };
  }
  const token = activeAdminToken();
  assert(token, "No active admin session is available for this probe");
  await context.addCookies([{ name: "nova_access_token", value: token, url: origin, sameSite: "Lax" }]);
  return { token, mode: "existing session" };
}

async function getTree(context, token, runId) {
  const response = await context.request.get(`${origin}/api/v1/agents/auto/runs/${runId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  assert(response.ok(), `Auto tree returned HTTP ${response.status()}`);
  return (await response.json()).runs;
}

async function getChildTimeline(context, token, rootRunId, childRunId) {
  const events = [];
  let after = -1;
  for (let page = 0; page < 100; page += 1) {
    const response = await context.request.get(
      `${origin}/api/v1/agents/auto/runs/${rootRunId}/children/${childRunId}/timeline?after=${after}&limit=100`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    assert(response.ok(), `Child timeline returned HTTP ${response.status()}`);
    const body = await response.json();
    events.push(...body.events);
    if (!body.has_more) return { ...body, events };
    assert(body.next_cursor > after, "Child timeline did not advance its pagination cursor");
    after = body.next_cursor;
  }
  throw new Error("Child timeline exceeded the 100-page probe bound");
}

async function waitForTree(context, token, runId, predicate, timeoutMs = 600000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const runs = await getTree(context, token, runId);
    if (predicate(runs)) return runs;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("Auto run did not reach the expected state before timeout");
}

async function waitForCatalogRecovery(context, token, timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  let lastStatus = "not checked";
  while (Date.now() < deadline) {
    const response = await context.request.get(`${origin}/api/v1/agents?studio=true`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    lastStatus = response.status();
    if (response.ok()) {
      const body = await response.json();
      assert(body.agents?.some((agent) => agent.agent_id === "__auto__"),
        "Recovered catalog did not include platform Auto");
      return Date.now();
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`Agent catalog did not recover within ${timeoutMs}ms (last HTTP ${lastStatus})`);
}

function assertNoUnhandledBrowserErrors(browserErrors, failedRequests, catalogRecoveredAt = 0) {
  const handledCatalogFailures = failedRequests.filter((failure) =>
    failure.status === 503 && failure.path === "/api/v1/agents" && failure.at <= catalogRecoveredAt);
  const unexpectedRequests = failedRequests.filter((failure) => !handledCatalogFailures.includes(failure));
  const unexpectedErrors = browserErrors.filter((error) =>
    !(handledCatalogFailures.length > 0 && error.at <= catalogRecoveredAt + 1000 &&
      error.text === "Failed to load resource: the server responded with a status of 503 (Service Unavailable)"));
  assert(unexpectedRequests.length === 0,
    `Unrecoverable API responses: ${unexpectedRequests.map((item) => `${item.status} ${item.path}`).join(" | ")}`);
  assert(unexpectedErrors.length === 0,
    `Browser errors: ${unexpectedErrors.map((item) => item.text).join(" | ")}`);
  if (handledCatalogFailures.length > 0) {
    console.log(`Handled initial catalog 503 responses: ${handledCatalogFailures.length}`);
  }
}

async function screenshot(page, label) {
  await page.screenshot({ path: path.join(outputDir, `${label}.png`), fullPage: false });
}

async function checkViewport(page, width, theme, label) {
  await page.setViewportSize({ width, height: 850 });
  await page.waitForTimeout(200);
  const layout = await page.evaluate(() => ({
    viewport: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    bodyWidth: document.body.scrollWidth,
    documentHeight: document.documentElement.scrollHeight,
    viewportHeight: window.innerHeight,
    theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
  }));
  assert(layout.theme === theme, `${label}: expected ${theme} theme, got ${layout.theme}`);
  assert(layout.documentWidth <= layout.viewport + 1, `${label}: document has horizontal overflow (${layout.documentWidth} > ${layout.viewport})`);
  assert(layout.bodyWidth <= layout.viewport + 1, `${label}: body has horizontal overflow (${layout.bodyWidth} > ${layout.viewport})`);
  assert(layout.documentHeight <= layout.viewportHeight + 1, `${label}: outer document scrolls vertically`);
  return layout;
}

async function setThemeAndReload(context, page, theme) {
  await context.addCookies([{ name: "vite-ui-theme", value: theme, url: origin, sameSite: "Lax" }]);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("textbox", { name: "Message Nova" }).waitFor();
}

async function waitForPanelSettled(page) {
  await page.waitForFunction(() => {
    const panel = document.querySelector('[data-testid="subagent-panel"]');
    if (!panel) return false;
    const rect = panel.getBoundingClientRect();
    return rect.width > 100 && rect.left >= -1 && rect.right <= window.innerWidth + 1 &&
      rect.top >= -1 && rect.bottom <= window.innerHeight + 1;
  }, null, { timeout: 5000 });
  const rect = await page.evaluate(() => {
    const panel = document.querySelector('[data-testid="subagent-panel"]');
    if (!panel) return null;
    const { x, width } = panel.getBoundingClientRect();
    return { x, width };
  });
  assert(rect, "The subagent panel detached before it settled");
  const panel = page.getByTestId("subagent-panel");
  const header = panel.locator("header");
  assert(await header.getByText("Subagents", { exact: true }).count(), "Compact Subagents tab is missing");
  assert(await header.getByRole("button", { name: "Back to subagents" }).count(), "Compact panel back control is missing");
  const close = header.getByRole("button", { name: "Close subagent panel" });
  const closeRect = await close.boundingBox();
  const minTarget = page.viewportSize().width <= 375 ? 44 : 36;
  assert(closeRect && closeRect.width >= minTarget && closeRect.height >= minTarget,
    `Subagent panel close target is smaller than ${minTarget}px (${JSON.stringify(closeRect)})`);
  if (page.viewportSize().width <= 375) {
    const railRight = await page.evaluate(() =>
      document.querySelector("aside.studio-sidebar")?.getBoundingClientRect().right ?? 0);
    assert(Math.abs(rect.x - railRight) <= 1 &&
      Math.abs(rect.x + rect.width - page.viewportSize().width) <= 1,
    `Mobile subagent panel does not fill the content area beside navigation (${JSON.stringify({ rect, railRight })})`);
    assert(!(await page.getByRole("textbox", { name: "Message Nova" }).isVisible()),
      "Mobile main composer is still visible behind the subagent panel");
  }
  const shell = await page.evaluate(() => ({
    bodyOverflow: document.body.style.overflow,
    bodyPointerEvents: document.body.style.pointerEvents,
    bodyScrollLocked: document.body.hasAttribute("data-scroll-locked"),
    dialogCount: document.querySelectorAll('[role="dialog"]').length,
    backdropCount: document.querySelectorAll('[data-slot="sheet-overlay"]').length,
  }));
  assert(!shell.bodyScrollLocked && !shell.bodyOverflow && !shell.bodyPointerEvents,
    `Subagent panel locked the outer document (${JSON.stringify(shell)})`);
  assert(shell.dialogCount === 0 && shell.backdropCount === 0,
    `Subagent panel opened modal UI (${JSON.stringify(shell)})`);
}

async function panelColors(page) {
  return page.evaluate(() => {
    const panel = document.querySelector('[data-testid="subagent-panel"]');
    if (!panel) return null;
    const title = panel.querySelector("header h2");
    const muted = panel.querySelector("header button");
    const status = panel.querySelector('.text-success-strong, .text-warning-strong, .text-destructive');
    const color = (element) => element ? getComputedStyle(element).color : null;
    return {
      theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
      background: getComputedStyle(panel).backgroundColor,
      title: color(title),
      muted: color(muted),
      status: color(status),
    };
  });
}

async function assertMainChatUsableBesidePanel(page, label) {
  if (page.viewportSize().width < 1024) return;
  const panel = page.getByTestId("subagent-panel");
  const chat = page.getByRole("textbox", { name: "Message Nova" });
  assert(await chat.isVisible(), `${label}: main composer disappeared at desktop width`);
  const panelRect = await panel.boundingBox();
  const chatRect = await chat.boundingBox();
  assert(panelRect && chatRect && chatRect.x + chatRect.width <= panelRect.x + 1,
    `${label}: child panel overlaps the main chat (${JSON.stringify({ panelRect, chatRect })})`);
  await chat.click();
  assert(await chat.evaluate((element) => document.activeElement === element),
    `${label}: main chat cannot receive focus while the child panel is open`);
  assert(await panel.isVisible(), `${label}: focusing main chat closed the child panel`);
}

async function assertPanelScrollOwnership(panel, label) {
  const before = await panel.evaluate((element) => {
    const header = element.querySelector("header");
    const timeline = element.querySelector('[aria-label="Subagent timeline"]');
    const footer = element.lastElementChild;
    return {
      panel: element.getBoundingClientRect().toJSON(),
      header: header?.getBoundingClientRect().toJSON(),
      footer: footer?.getBoundingClientRect().toJSON(),
      clientHeight: timeline?.clientHeight ?? 0,
      scrollHeight: timeline?.scrollHeight ?? 0,
    };
  });
  assert(before.header && before.footer && before.clientHeight > 0,
    `${label}: the panel lacks a bounded header, timeline, or footer`);
  if (before.scrollHeight <= before.clientHeight + 10) return;
  const timeline = panel.locator('[aria-label="Subagent timeline"]');
  await timeline.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  const after = await panel.evaluate((element) => ({
    headerTop: element.querySelector("header")?.getBoundingClientRect().top,
    footerBottom: element.lastElementChild?.getBoundingClientRect().bottom,
    timelineScrollTop: element.querySelector('[aria-label="Subagent timeline"]')?.scrollTop,
  }));
  assert(after.timelineScrollTop > 0, `${label}: long child content did not scroll inside the panel`);
  assert(Math.abs(after.headerTop - before.header.top) <= 1 &&
    Math.abs(after.footerBottom - before.footer.bottom) <= 1,
  `${label}: scrolling child activity moved the header or composer`);
}

async function assertPersistedActivityVisible(panel, timeline, phase) {
  const samples = timeline.events.flatMap((event) => {
    if (event.type !== "child_activity") return [];
    const payload = event.payload ?? {};
    const kind = String(payload.event_type ?? "");
    if (kind === "plan") {
      const step = Array.isArray(payload.steps) ? payload.steps.find((item) => item?.text) : null;
      return step ? [{ kind, value: String(step.text) }] : payload.text ? [{ kind, value: String(payload.text) }] : [];
    }
    if (kind === "thinking" && payload.text) return [{ kind, value: String(payload.text) }];
    if (kind === "tool_call" && payload.tool_name) return [{ kind, value: String(payload.tool_name) }];
    if (["tool_progress", "tool_detail"].includes(kind) && (payload.text || payload.stage)) {
      return [{ kind, value: String(payload.text || payload.stage) }];
    }
    if (kind === "tool_status" && payload.status) return [{ kind, value: String(payload.status) }];
    return [];
  });
  const kinds = [...new Set(timeline.events.filter((event) => event.type === "child_activity")
    .map((event) => String(event.payload?.event_type ?? "")))];
  console.log(`${phase} persisted child activity: ${kinds.join(", ") || "none"}`);
  assert(samples.length > 0, `${phase}: no persisted plan, Working, or tool activity was available`);
  let missing = samples;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const visible = (await panel.locator('[aria-label="Subagent timeline"]').innerText())
      .replace(/\s+/g, " ");
    missing = samples.filter((sample) => !visible.includes(sample.value.replace(/\s+/g, " ")));
    if (missing.length === 0) break;
    await new Promise((resolve) => setTimeout(resolve, 750));
  }
  assert(missing.length === 0,
    `${phase}: persisted activity missing from panel: ${missing.map((item) =>
      `${item.kind}(${item.value.slice(0, 80)})`).join(", ")}`);
}

async function openSalesPanel(page, label, source = "card", expectedTimeline = null) {
  const opener = source === "rail"
    ? page.getByRole("button", { name: /Called .+\. Open subagent conversation/ }).first()
    : page.getByRole("region", { name: "Auto agent activity" }).getByRole("button", { name: /Open .+ conversation/ }).first();
  await opener.waitFor({ timeout: 30000 });
  await opener.focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  assert(await opener.evaluate((element) => document.activeElement === element), `${label}: subagent control is not reachable by Tab`);
  await page.keyboard.press("Enter");
  const panel = page.getByTestId("subagent-panel");
  await panel.waitFor({ timeout: 15000 });
  await waitForPanelSettled(page);
  const theme = await page.evaluate(() => document.documentElement.classList.contains("dark") ? "dark" : "light");
  await checkViewport(page, page.viewportSize().width, theme, `${label}-panel-open`);
  await assertMainChatUsableBesidePanel(page, label);
  const timeline = panel.locator('[aria-label="Subagent timeline"]');
  await timeline.getByText("Main assigned a task").waitFor({ timeout: 30000 });
  assert(await timeline.getByText(/started working\./).count(), `${label}: child start event is absent from panel`);
  if (process.env.NOVA_STUDIO_EXPECT_STEER === "1") {
    assert(await timeline.getByText(/You to Sales Agent/).count(), `${label}: user-to-child message is absent from replay`);
  }
  await timeline.getByText(answerCue).first().waitFor({ timeout: 30000 });
  if (expectAnswerTable) {
    assert(await timeline.locator("table").count(), `${label}: child answer table is not rendered as a table`);
  }
  if (expectedTimeline) await assertPersistedActivityVisible(panel, expectedTimeline, label);
  if (expectedTimeline?.events.some((event) => event.type === "agent_completed")) {
    await timeline.getByText("Sales Agent finished.").waitFor({ timeout: 15000 });
  }
  await assertPanelScrollOwnership(panel, label);
  if (label === "04-replay-child-from-main-rail" || label === "replay-light-375-panel") {
    console.log(`Panel colors ${label}: ${JSON.stringify(await panelColors(page))}`);
  }
  const back = panel.getByRole("button", { name: "Back to subagents" });
  const close = panel.getByRole("button", { name: "Close subagent panel" });
  await close.focus();
  await page.keyboard.press("Tab");
  assert(await back.evaluate((element) => element === document.activeElement),
    `${label}: compact panel back control is not keyboard reachable`);
  await back.click();
  await panel.getByRole("heading", { name: "All subagents" }).waitFor();
  await panel.getByRole("button", { name: /Open Sales Agent conversation/ }).waitFor();
  await panel.getByRole("button", { name: "Back to conversation" }).click();
  await timeline.waitFor();
  await screenshot(page, label);
  if (label.includes("main-rail") || label.includes("dark-320") || label.includes("light-375")) {
    await timeline.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    await screenshot(page, `${label}-answer`);
  }
  await panel.getByRole("button", { name: "Close subagent panel" }).focus();
  await page.keyboard.press("Escape");
  await panel.waitFor({ state: "detached", timeout: 10000 });
  return timeline;
}

async function checkReplay(context, page, token, threadId, rootRunId = null, childRunId = null) {
  const expectedTimeline = rootRunId && childRunId
    ? await getChildTimeline(context, token, rootRunId, childRunId)
    : null;
  const replayUrl = `${origin}/studio?agent=__auto__&thread=${encodeURIComponent(threadId)}`;
  await page.goto(replayUrl, { waitUntil: "domcontentloaded" });
  await page.getByRole("region", { name: "Auto agent activity" }).getByRole("button", { name: "Open Sales Agent conversation" }).waitFor({ timeout: 30000 });
  await page.getByText(answerCue).first().waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: "Called Sales Agent. Open subagent conversation" }).waitFor({ timeout: 30000 });
  const history = page.locator("aside.studio-sidebar");
  await history.locator("ul > li").first().waitFor({ timeout: 30000 });
  assert(!(await history.getByText("No conversations yet. Ask something and it will appear here.", { exact: true }).count()),
    "Saved chat history was replaced with an empty state during replay");
  assert(!(await page.getByRole("heading", { name: "Nova Studio" }).count()), "Nova Studio header is still visible");
  assert(!(await page.getByRole("button", { name: "Ask Nove" }).count()), "Ask Nove control is still visible");
  assert(!(await page.getByText("Nove Assistant", { exact: true }).count()), "Nove Assistant panel is still visible");
  await screenshot(page, "03-replay-desktop-dark");
  await openSalesPanel(page, "04-replay-child-from-main-rail", "rail", expectedTimeline);
  await openSalesPanel(page, "05-replay-child-from-card", "card", expectedTimeline);

  for (const theme of ["dark", "light"]) {
    await setThemeAndReload(context, page, theme);
    await page.getByRole("region", { name: "Auto agent activity" }).getByRole("button", { name: /Open .+ conversation/ }).first().waitFor({ timeout: 30000 });
    for (const width of [320, 375, 1440]) {
      const label = `replay-${theme}-${width}`;
      await checkViewport(page, width, theme, label);
      await openSalesPanel(page, `${label}-panel`, "card", expectedTimeline);
      await checkViewport(page, width, theme, `${label}-after-panel`);
    }
  }
}

async function runCancellationProbe(context, page, token, rootRunId, childRunId, threadId) {
  await page.getByRole("region", { name: "Auto agent activity" })
    .getByRole("button", { name: "Open Sales Agent conversation" }).click();
  const panel = page.getByTestId("subagent-panel");
  await panel.waitFor({ timeout: 15000 });
  await waitForPanelSettled(page);
  await panel.getByRole("button", { name: "Cancel agent" }).waitFor({ timeout: 15000 });
  await screenshot(page, "cancel-01-child-before-action");

  const cancelResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" &&
    response.url().includes(`/auto/runs/${rootRunId}/children/${childRunId}/cancel`),
  { timeout: 30000 });
  await panel.getByRole("button", { name: "Cancel agent" }).click();
  const cancelResponse = await cancelResponsePromise;
  assert(cancelResponse.status() === 200, `Cancel agent returned HTTP ${cancelResponse.status()}`);
  assert((await cancelResponse.json()).status === "cancelled", "Cancel agent response was not cancelled");

  const cancelledRuns = await waitForTree(context, token, rootRunId, (runs) =>
    runs.some((run) => run.run_id === childRunId && run.status === "cancelled"), 60000);
  assert(cancelledRuns.find((run) => run.run_id === childRunId)?.agent_name === "Sales Agent",
    "Cancelled child lost its saved Sales Agent name");
  let childTimeline;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    childTimeline = await getChildTimeline(context, token, rootRunId, childRunId);
    if (childTimeline.events.some((event) => event.type === "agent_cancelled")) break;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  assert(childTimeline?.events.some((event) => event.type === "agent_cancelled"),
    "Child timeline has no cancellation event");
  assert(!childTimeline.events.some((event) => event.type === "agent_completed"),
    "Cancelled child also has a completion event");
  await panel.getByText("Sales Agent cancelled.").waitFor({ timeout: 30000 });
  await panel.getByText("This subagent has cancelled. The conversation is read only.").waitFor({ timeout: 30000 });
  await screenshot(page, "cancel-02-child-after-action");
  await panel.getByRole("button", { name: "Close subagent panel" }).focus();
  await page.keyboard.press("Escape");
  await panel.waitFor({ state: "detached", timeout: 10000 });

  const terminalRuns = await waitForTree(context, token, rootRunId, (runs) =>
    runs.some((run) => run.depth === 0 && terminalStatuses.has(run.status)), 180000);
  const root = terminalRuns.find((run) => run.depth === 0);
  assert(root, "Cancelled-child Auto root is missing");
  console.log(`Cancel root reached ${root.status}; child remained cancelled`);

  await page.goto(`${origin}/studio?agent=__auto__&thread=${encodeURIComponent(threadId)}`, { waitUntil: "domcontentloaded" });
  const history = page.locator("aside.studio-sidebar");
  await history.locator("ul > li").first().waitFor({ timeout: 30000 });
  assert(!(await history.getByText("No conversations yet. Ask something and it will appear here.", { exact: true }).count()),
    "Cancelled run disappeared from saved history");
  const cancelledCard = page.getByRole("region", { name: "Auto agent activity" })
    .getByRole("button", { name: "Open Sales Agent conversation" }).filter({ hasText: "Cancelled" }).first();
  await cancelledCard.waitFor({ timeout: 30000 });
  await cancelledCard.click();
  const replayPanel = page.getByTestId("subagent-panel");
  await replayPanel.waitFor({ timeout: 15000 });
  await waitForPanelSettled(page);
  await replayPanel.getByText("Sales Agent cancelled.").waitFor({ timeout: 30000 });
  await replayPanel.getByText("This subagent has cancelled. The conversation is read only.").waitFor({ timeout: 30000 });
  assert(!(await replayPanel.getByRole("button", { name: "Cancel agent" }).count()),
    "Cancel action is still offered after replaying a cancelled child");
  assert(!(await page.getByRole("heading", { name: "Nova Studio" }).count()), "Nova Studio header is still visible");
  assert(!(await page.getByText("Nove Assistant", { exact: true }).count()), "Nove Assistant panel is still visible");
  await screenshot(page, "cancel-03-replay-child");
  await replayPanel.getByRole("button", { name: "Close subagent panel" }).focus();
  await page.keyboard.press("Escape");
  await replayPanel.waitFor({ state: "detached", timeout: 10000 });
  console.log(`Cancelled child timeline: ${childTimeline.events.length} events`);
}

async function main() {
  fs.mkdirSync(outputDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 850 } });
  const page = await context.newPage();
  const browserErrors = [];
  const failedRequests = [];
  page.on("pageerror", (error) => browserErrors.push({ text: `page: ${error.message}`, at: Date.now() }));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push({ text: message.text(), at: Date.now() });
  });
  page.on("response", (response) => {
    if (response.status() >= 400 && new URL(response.url()).pathname.startsWith("/api/")) {
      failedRequests.push({ status: response.status(), path: new URL(response.url()).pathname, at: Date.now() });
    }
  });

  try {
    const { token, mode } = await signIn(context, page);
    await context.addCookies([{ name: "vite-ui-theme", value: "dark", url: origin, sameSite: "Lax" }]);
    if (process.env.NOVA_STUDIO_REPLAY_ONLY === "1") {
      const existingThread = process.env.NOVA_STUDIO_REPLAY_THREAD;
      assert(existingThread, "NOVA_STUDIO_REPLAY_THREAD is required for replay-only mode");
      if (process.env.NOVA_STUDIO_REPLAY_RUN && process.env.NOVA_STUDIO_REPLAY_CHILD) {
        const childTimeline = await getChildTimeline(context, token,
          process.env.NOVA_STUDIO_REPLAY_RUN, process.env.NOVA_STUDIO_REPLAY_CHILD);
        const eventTypes = new Set(childTimeline.events.map((event) => event.type));
        for (const expectedType of ["agent_started", "child_activity", "agent_completed"]) {
          assert(eventTypes.has(expectedType), `Replay child timeline has no ${expectedType} event`);
        }
        if (process.env.NOVA_STUDIO_EXPECT_STEER === "1") {
          assert(eventTypes.has("agent_message"), "Replay child timeline has no user-to-child message event");
        }
        console.log(`Replay child timeline: ${childTimeline.events.length} events, ${[...eventTypes].join(", ")}`);
      }
      await checkReplay(context, page, token, existingThread,
        process.env.NOVA_STUDIO_REPLAY_RUN, process.env.NOVA_STUDIO_REPLAY_CHILD);
      assertNoUnhandledBrowserErrors(browserErrors, failedRequests);
      console.log(`PASS Studio Auto replay browser probe (${mode})`);
      console.log(`Thread: ${existingThread}`);
      console.log(`Screenshots: ${outputDir}`);
      return;
    }
    await page.goto(`${origin}/studio?agent=__auto__`, { waitUntil: "domcontentloaded" });
    await page.getByRole("textbox", { name: "Message Nova" }).waitFor({ timeout: 20000 });
    const catalogRecoveredAt = await waitForCatalogRecovery(context, token);
    await page.getByRole("button", { name: "Auto", exact: true }).waitFor({ timeout: 30000 });
    await page.waitForFunction(() => {
      const composer = document.querySelector('textarea[aria-label="Message Nova"]');
      return composer && !composer.disabled;
    }, null, { timeout: 30000 });
    await page.getByRole("button", { name: "New chat" }).first().click();
    await page.getByRole("button", { name: "Auto", exact: true }).waitFor({ timeout: 30000 });
    await page.waitForFunction(() => {
      const composer = document.querySelector('textarea[aria-label="Message Nova"]');
      return composer && !composer.disabled;
    }, null, { timeout: 30000 });
    await page.getByRole("textbox", { name: "Message Nova" }).fill(question);

    const responsePromise = page.waitForResponse((response) =>
      response.request().method() === "POST" &&
      /\/agents\/__auto__\/threads\/[^/]+\/messages(?:\?|$)/.test(response.url()),
    { timeout: 30000 });
    await page.getByRole("button", { name: "Send", exact: true }).click();
    const streamResponse = await responsePromise;
    assert(streamResponse.status() === 200, `Auto stream returned HTTP ${streamResponse.status()}`);
    const runId = streamResponse.headers()["x-nova-run-id"];
    assert(runId, "Auto stream did not expose a durable root run ID");
    const threadId = new URL(streamResponse.url()).pathname.match(/\/threads\/([^/]+)\/messages/)?.[1];
    assert(threadId, "Auto stream did not include a thread ID");
    console.log(`Auto browser run started: ${runId}`);
    console.log(`Thread started: ${threadId}`);

    const activeRuns = await waitForTree(context, token, runId, (runs) =>
      runs.some((run) => run.depth === 1), 120000);
    assert(activeRuns.some((run) => run.depth === 1), "Auto never spawned a child agent");
    await page.getByRole("region", { name: "Auto agent activity" }).getByRole("button", { name: "Open Sales Agent conversation" }).waitFor({ timeout: 30000 });
    await page.getByRole("button", { name: "Called Sales Agent. Open subagent conversation" }).waitFor({ timeout: 30000 });
    const activeMain = await page.locator("main").innerText();
    assert(!activeMain.includes("Starting analysis…"), "Main still says Starting analysis after the child was called");
    await screenshot(page, "01-active-desktop-dark");

    const activeChild = activeRuns.find((run) => run.depth === 1 && run.agent_id === expectedChildAgentId);
    assert(activeChild, "Auto spawned no Sales Agent child");
    console.log(`Sales child observed: ${activeChild.run_id} (${activeChild.status})`);
    if (process.env.NOVA_STUDIO_CANCEL_PROBE === "1") {
      assert(!terminalStatuses.has(activeChild.status), "Sales child finished before the cancellation probe could act");
      await runCancellationProbe(context, page, token, runId, activeChild.run_id, threadId);
      assertNoUnhandledBrowserErrors(browserErrors, failedRequests, catalogRecoveredAt);
      console.log(`PASS Studio Auto cancellation browser probe (${mode})`);
      console.log(`Run: ${runId}`);
      console.log(`Child: ${activeChild.run_id}`);
      console.log(`Thread: ${threadId}`);
      console.log(`Screenshots: ${outputDir}`);
      return;
    }
    if (!terminalStatuses.has(activeChild.status)) {
      await page.getByRole("region", { name: "Auto agent activity" }).getByRole("button", { name: /Open .+ conversation/ }).click();
      const panel = page.getByTestId("subagent-panel");
      await panel.waitFor();
      await waitForPanelSettled(page);
      await assertMainChatUsableBesidePanel(page, "01-active-child-panel-dark");
      const composer = panel.getByRole("textbox", { name: "Message subagent" });
      await composer.waitFor({ timeout: 30000 });
      await screenshot(page, "01-active-child-panel-dark");
      await composer.fill(steerText);
      const messageResponse = page.waitForResponse((response) =>
        response.request().method() === "POST" &&
        response.url().includes(`/auto/runs/${runId}/children/${activeChild.run_id}/messages`),
      { timeout: 30000 });
      await panel.getByRole("button", { name: "Send to subagent" }).click();
      assert((await messageResponse).status() === 200, "Sending context to the subagent failed");
      await panel.getByText(/You to /).waitFor({ timeout: 30000 });
      console.log("User-to-child message is visible in the panel");
      await screenshot(page, "01-child-message-dark");
      await panel.getByRole("button", { name: "Close subagent panel" }).focus();
      await page.keyboard.press("Escape");
      await panel.waitFor({ state: "detached", timeout: 10000 });
    }

    const completedRuns = await waitForTree(context, token, runId, (runs) =>
      runs.some((run) => run.depth === 0 && terminalStatuses.has(run.status)));
    const root = completedRuns.find((run) => run.depth === 0);
    const salesChild = completedRuns.find((run) => run.depth === 1 && run.agent_id === expectedChildAgentId);
    assert(root?.status === "completed", `Auto root ended ${root?.status}`);
    assert(salesChild?.status === "completed", `Sales Agent child ended ${salesChild?.status ?? "missing"}`);
    console.log("Auto root and Sales Agent child completed");
    const childTimeline = await getChildTimeline(context, token, runId, salesChild.run_id);
    const childEventTypes = new Set(childTimeline.events.map((event) => event.type));
    assert(childEventTypes.has("agent_started"), "Child timeline has no start event");
    assert(childEventTypes.has("child_activity"), "Child timeline has no harness activity");
    assert(childEventTypes.has("agent_completed"), "Child timeline has no completion event");
    await page.getByRole("region", { name: "Auto agent activity" })
      .getByRole("button", { name: "Open Sales Agent conversation" }).click();
    const freshPanel = page.getByTestId("subagent-panel");
    await freshPanel.waitFor({ timeout: 15000 });
    await freshPanel.getByText("Main assigned a task").waitFor({ timeout: 30000 });
    await assertPersistedActivityVisible(freshPanel, childTimeline, "fresh completed Auto run");
    await freshPanel.getByText("Sales Agent finished.").waitFor({ timeout: 15000 });
    await screenshot(page, "02-fresh-persisted-child-activity-dark");
    await freshPanel.getByRole("button", { name: "Close subagent panel" }).click();
    await freshPanel.waitFor({ state: "detached", timeout: 10000 });
    await page.getByText(answerCue).first().waitFor({ timeout: 30000 });
    const mainText = await page.locator("main").innerText();
    assert(!mainText.includes("Could not verify every number"), "Auto answer fell back to numeric verification error");
    assert(!mainText.includes("Starting analysis…"), "Main transcript remained at Starting analysis after completion");
    assert(!(await page.getByRole("heading", { name: "Nova Studio" }).count()), "Nova Studio header is still visible");
    assert(!(await page.getByRole("button", { name: "Ask Nove" }).count()), "Ask Nove control is still visible");
    assert(!(await page.getByText("Nove Assistant", { exact: true }).count()), "Nove Assistant panel is still visible");
    await screenshot(page, "02-completed-desktop-dark");

    await checkReplay(context, page, token, threadId, runId, salesChild.run_id);

    assertNoUnhandledBrowserErrors(browserErrors, failedRequests, catalogRecoveredAt);
    console.log(`PASS Studio Auto browser probe (${mode})`);
    console.log(`Run: ${runId}`);
    console.log(`Thread: ${threadId}`);
    console.log(`Screenshots: ${outputDir}`);
  } catch (error) {
    await screenshot(page, "failure-state").catch(() => undefined);
    const visibleText = await page.locator("body").innerText().catch(() => "");
    console.error(`Visible UI: ${visibleText.slice(0, 1600).replaceAll("\n", " | ")}`);
    console.error(`Failed API requests: ${failedRequests.map((item) => `${item.status} ${item.path}`).join(" | ") || "none"}`);
    console.error(`Browser errors: ${browserErrors.map((item) => item.text).join(" | ") || "none"}`);
    throw error;
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => {
  console.error(`FAIL Studio Auto browser probe: ${error.message}`);
  console.error(`Screenshots: ${outputDir}`);
  process.exitCode = 1;
});
