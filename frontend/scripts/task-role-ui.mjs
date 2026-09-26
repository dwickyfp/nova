import { chromium } from "playwright";
import fs from "node:fs/promises";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
input = "";
const browser = await chromium.launch({ headless: true });
const records = [];
async function login(account) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(120000);
  await page.goto(`${config.url}/sign-in`);
  await page.getByLabel("Username", { exact: true }).fill(account.username);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL((url) => !url.pathname.includes("sign-in"));
  await page.goto(`${config.url}/tasks`);
  await page.getByPlaceholder("Search task, database, schedule...").waitFor();
  return { context, page };
}
try {
  const { page, context } = await login(config.runner);
  for (const task of config.tasks) {
    const name = task.name;
    await page
      .getByPlaceholder("Search task, database, schedule...")
      .fill(name);
    const button = page.getByRole("button", {
      name: `Run ${name}`,
      exact: true,
    });
    const expected = task.expected ?? "success";
    const accepted = page.waitForResponse(
      (r) =>
        r.url().includes("/task-orchestration/graphs/") &&
        r.request().method() === "POST",
    );
    await button.click();
    const response = await accepted;
    const run = await response.json();
    if (response.status() !== 202) {
      records.push({
        task: name,
        passed: false,
        http_status: response.status(),
        detail: run.detail,
      });
      continue;
    }
    const settled = await page.waitForResponse(
      async (r) => {
        if (
          !r.url().endsWith("/task-orchestration/graphs") ||
          r.request().method() !== "GET" ||
          r.status() !== 200
        )
          return false;
        const data = await r.json();
        return data.graphs.some(
          (g) =>
            g.last_run?.id === run.id &&
            ["success", "failed"].includes(g.last_run.state),
        );
      },
      { timeout: 90000 },
    );
    const graph = (await settled.json()).graphs.find(
      (g) => g.last_run?.id === run.id,
    );
    const row = page
      .getByRole("link")
      .filter({ has: page.getByText(name, { exact: true }) });
    await row.getByText(graph.last_run.state, { exact: true }).waitFor();
    const record = {
      task: name,
      run_id: run.id,
      state: graph.last_run.state,
      execution_user: run.execution_user,
      execution_role: run.execution_role,
      passed:
        graph.last_run.state === expected &&
        run.execution_user === config.runner.username &&
        run.execution_role === config.role,
    };
    if (graph.last_run.state === "failed") {
      await row.getByRole("button", { name: "View error" }).click();
      const dialog = page.getByRole("dialog", { name: "Run error" });
      await dialog
        .getByText("Loading", { exact: true })
        .waitFor({ state: "hidden" });
      record.error = await dialog.innerText();
      if (task.error) record.passed &&= record.error.includes(task.error);
      await dialog.screenshot({ path: `${config.output}/${name}-error.png` });
      await page.keyboard.press("Escape");
      await page.getByRole("dialog").waitFor({ state: "hidden" });
    }
    records.push(record);
    console.log(JSON.stringify({ progress: record }));
  }
  for (const task of config.reviewErrors ?? []) {
    await page
      .getByPlaceholder("Search task, database, schedule...")
      .fill(task.name);
    const row = page
      .getByRole("link")
      .filter({ has: page.getByText(task.name, { exact: true }) });
    await row.getByRole("button", { name: "View error" }).click();
    const dialog = page.getByRole("dialog", { name: "Run error" });
    await dialog.getByText(task.error, { exact: false }).waitFor();
    records.push({
      case: `${task.name}_error_visible`,
      passed: true,
      error: await dialog.innerText(),
    });
    await dialog.screenshot({
      path: `${config.output}/${task.name}-error.png`,
    });
    await page.keyboard.press("Escape");
    await page.getByRole("dialog").waitFor({ state: "hidden" });
  }
  await page
    .getByPlaceholder("Search task, database, schedule...")
    .fill("chain_a");
  await page
    .getByRole("link")
    .filter({ has: page.getByText("chain_a", { exact: true }) })
    .click();
  await page
    .locator(".react-flow__node")
    .filter({ has: page.getByText("chain_a", { exact: true }) })
    .click();
  await page.getByRole("button", { name: "View SQL for chain_a" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator("code").waitFor();
  const sqlText = await dialog.innerText();
  records.push({
    case: "node_sql_modal",
    passed: sqlText.includes("INSERT INTO NOVA_TASK_DEMO.chain_a"),
  });
  await dialog.screenshot({ path: `${config.output}/sql-modal.png` });
  await page.keyboard.press("Escape");
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page.screenshot({ path: `${config.output}/chain-detail.png` });
  await context.close();
  const denied = await login(config.outsider);
  await denied.page.getByText("No tasks yet", { exact: true }).waitFor();
  records.push({ case: "different_role_cannot_list_tasks", passed: true });
  await denied.page.screenshot({ path: `${config.output}/other-role.png` });
  await denied.context.close();
} catch (error) {
  records.push({
    case: "browser_workflow",
    passed: false,
    error_type: error.name,
    message: String(error.message)
      .replaceAll(config.runner.password, "[redacted]")
      .replaceAll(config.outsider.password, "[redacted]"),
  });
} finally {
  await fs.writeFile(
    `${config.output}/ui-results.json`,
    JSON.stringify(records, null, 2),
  );
  await browser.close();
}
console.log(
  JSON.stringify({
    complete: true,
    passed: records.filter((r) => r.passed).length,
    total: records.length,
  }),
);
process.exitCode = records.every((r) => r.passed) ? 0 : 1;
