import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

if (process.env.NOVA_INTELLIGENCE_BROWSER_ACCEPTANCE !== '1') {
  throw new Error('Opt in to the isolated Studio browser acceptance');
}
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const { chromium } = await import(path.join(root, 'frontend/node_modules/playwright/index.mjs'));
const base = process.env.NOVA_BROWSER_FRONTEND || 'http://127.0.0.1:5174';
const backend = process.env.NOVA_BROWSER_API || 'http://127.0.0.1:58012';
for (const endpoint of [base, backend]) {
  assert(['127.0.0.1', 'localhost'].includes(new URL(endpoint).hostname));
}
const fixture = JSON.parse(await fs.readFile(process.env.NOVA_BROWSER_FIXTURE, 'utf8'));
const destination = process.env.NOVA_BROWSER_OUTPUT;
assert(destination);
await fs.mkdir(destination, { recursive: true });
const browser = await chromium.launch({ headless: true });
const results = [];
try {
  for (const [name, width, height, theme] of [
    ['desktop', 1280, 900, 'light'],
    ['mobile', 360, 740, 'dark'],
  ]) {
    const context = await browser.newContext({ viewport: { width, height }, colorScheme: theme });
    await context.addCookies([{ name: 'vite-ui-theme', value: theme, url: base }]);
    const page = await context.newPage();
    let pageErrors = 0;
    page.on('pageerror', () => { pageErrors += 1; });
    // Forward real responses to the isolated API without changing payloads or authorization.
    await page.route('**/api/**', async (route) => {
      try {
        const original = new URL(route.request().url());
        const response = await route.fetch({
          url: backend + original.pathname + original.search,
          timeout: 120000,
        });
        await route.fulfill({ response });
      } catch {
        if (!page.isClosed()) await route.abort().catch(() => {});
      }
    });
    await page.goto(base + '/studio?view=news');
    await page.getByLabel('Username', { exact: true }).fill(process.env.NOVA_BROWSER_USER);
    await page.getByLabel('Password', { exact: true }).fill(process.env.NOVA_BROWSER_PASSWORD);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL('**/studio?view=news', { timeout: 120000 });
    // The API fixture was created after an explicit activation of this same role.
    await page.evaluate(async (role) => {
      const { api } = await import('/src/lib/api-client.ts');
      await api.post('/auth/switch-role', { role });
    }, fixture.role || 'marketing');
    if (fixture.stage === 'populated') {
      await page.goto(base + '/studio?view=news&item=' + fixture.news_id);
      await page.getByRole('heading', { name: 'Ranger revenue', exact: false }).waitFor({ timeout: 120000 });
      await page.getByText('Ranked contributions and hypotheses', { exact: true }).waitFor({ timeout: 120000 });
      await page.locator('summary').filter({ hasText: /^Evidence \(/ }).click();
      await page.getByText('query · semantic-compiler-v1', { exact: true }).first().waitFor();
      await page.screenshot({ path: path.join(destination, `${name}-news.png`), fullPage: true });
      await page.goto(base + '/studio?view=decisions&item=' + fixture.decision_id);
      await page.getByRole('heading', { name: 'Ranger governed recovery', exact: true }).waitFor({ timeout: 120000 });
      const select = page.getByRole('button', { name: 'Select option', exact: true });
      await select.waitFor({ timeout: 120000 });
      await select.focus();
      assert(await select.evaluate((el) => el === document.activeElement));
      await page.keyboard.press('Enter');
      await page.getByText('REQUIRE APPROVAL', { exact: true }).waitFor({ timeout: 120000 });
      assert.equal(await page.getByRole('button', { name: 'Approve this revision' }).count(), 0);
      await page.getByText('Lineage', { exact: true }).waitFor({ timeout: 120000 });
      const lineage = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Lineage', exact: true }) });
      await lineage.getByText(/→ Investigation → Decision revision/).waitFor({ timeout: 120000 });
      await lineage.locator('summary').filter({ hasText: /^Evidence \(/ }).click();
      await lineage.getByText('query · semantic-compiler-v1', { exact: true }).first().waitFor();
      await page.locator('summary').filter({ hasText: /^Assumptions$/ }).click();
      await page.screenshot({ path: path.join(destination, `${name}-decision.png`), fullPage: true });
    } else if (fixture.stage === 'stale') {
      await page.goto(base + '/studio?view=decisions&item=' + fixture.decision_id);
      await page.getByText('Evidence needs revalidation', { exact: true }).waitFor({ timeout: 120000 });
      assert.equal(await page.getByRole('button', { name: 'Select option' }).count(), 0);
      await page.screenshot({ path: path.join(destination, `${name}-stale.png`), fullPage: true });
    } else if (fixture.stage === 'empty_denied') {
      await page.goto(base + '/studio?view=decisions');
      await page.getByLabel('Decision visibility').getByRole('button', { name: 'Shared with me', exact: true }).click();
      await page.getByText('No decisions yet', { exact: true }).waitFor({ timeout: 120000 });
      await page.screenshot({ path: path.join(destination, `${name}-empty.png`), fullPage: true });
      await page.goto(base + '/studio?view=decisions&item=' + encodeURIComponent(fixture.decision_id));
      await page.getByText('This item is unavailable with your current access', { exact: true }).waitFor({ timeout: 120000 });
      const retry = page.getByRole('button', { name: 'Retry', exact: true });
      await retry.focus();
      assert(await retry.evaluate((el) => el === document.activeElement));
      await page.keyboard.press('Enter');
      await page.getByText('This item is unavailable with your current access', { exact: true }).waitFor({ timeout: 120000 });
      await page.screenshot({ path: path.join(destination, `${name}-denied.png`), fullPage: true });
    } else {
      throw new Error('Unknown browser checkpoint');
    }
    const dimensions = await page.evaluate(() => ({
      width: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      height: innerHeight,
      documentHeight: document.documentElement.scrollHeight,
    }));
    assert(dimensions.documentWidth <= dimensions.width + 1, JSON.stringify(dimensions));
    assert(dimensions.documentHeight <= dimensions.height + 1, JSON.stringify(dimensions));
    assert.equal(pageErrors, 0);
    results.push({ stage: fixture.stage, name, theme, dimensions, pageErrors });
    await page.evaluate(async () => {
      const { api } = await import('/src/lib/api-client.ts');
      await api.post('/auth/logout');
    });
    await context.close();
  }
  await fs.writeFile(path.join(destination, `${fixture.stage}.json`), JSON.stringify(results, null, 2) + '\n');
  console.log(JSON.stringify(results));
} finally {
  await browser.close();
}
