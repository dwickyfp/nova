import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(path.join(process.cwd(), 'package.json'))
const { createServer } = await import(pathToFileURL(require.resolve('vite')).href)
const { chromium } = require('playwright')
const chunks = []
for await (const chunk of process.stdin) chunks.push(chunk)
const input = JSON.parse(Buffer.concat(chunks).toString())
const proxy = { target: input.api, changeOrigin: true }
const vite = await createServer({
  logLevel: 'silent',
  server: { host: '127.0.0.1', port: 0, proxy: {
    '^/api/v1/(query|ml|assistant|agents|intelligence/actions)(/|$)': proxy,
    '/api': proxy,
  } },
})
let browser
let page
try {
  await vite.listen()
  const origin = `http://127.0.0.1:${vite.httpServer.address().port}`
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' })
  await context.addCookies([{ name: 'nova_access_token', value: input.token, url: origin, sameSite: 'Lax' }])
  page = await context.newPage()
  page.setDefaultTimeout(45000)
  if (input.mode === 'live-plan') {
    await page.goto(`${origin}/studio/reports`)
    await page.getByLabel('What do you want to understand?').fill('Create a report titled Live revenue using the published governed_revenue source. Include one KPI for total revenue and one bar chart grouped by city. Add a single-selection City filter defaulting to Jakarta, applied to both widgets. Use unique IDs for pages, sections, and widgets. Do not add time filters or calculations. Keep it a private draft.')
    const planned = page.waitForResponse(r => r.url().endsWith('/reports/plan') && r.request().method() === 'POST', { timeout: 180000 })
    await page.getByRole('button', { name: 'Plan report', exact: true }).click()
    const response = await planned
    assert.equal(response.status(), 200, 'Live model report proposal failed')
    const proposal = await response.json()
    assert.ok(proposal.spec, 'Live model must produce a valid report for this unambiguous source')
    await page.getByRole('button', { name: 'Create private draft', exact: true }).click()
    await page.waitForURL(url => url.pathname.startsWith('/studio/reports/'))
    await page.locator('.report-widget [title="1980.00"]').waitFor({ timeout: 60000 })
    await page.screenshot({ path: input.screenshot, fullPage: true, animations: 'disabled' })
    console.log(JSON.stringify({ status: 'passed', journey: 'live-model-plan-create-governed-render', report_id: new URL(page.url()).pathname.split('/').at(-1) }))
  } else if (input.mode === 'prompt') {
    await page.goto(`${origin}/studio?agent=${input.origin.agent_id}&thread=${input.origin.thread_id}`)
    await page.getByRole('button', { name: input.agent_name, exact: true }).waitFor()
    await page.getByRole('textbox', { name: 'Message Nova' }).fill('Create a city revenue report filtered to Jakarta')
    await page.getByRole('button', { name: 'Send', exact: true }).click()
    await page.getByRole('button', { name: 'Allow once', exact: true }).waitFor({ timeout: 90000 })
    assert.equal(await page.getByRole('button', { name: 'Allow for this chat', exact: true }).count(), 0)
    await page.getByRole('button', { name: 'Allow once', exact: true }).click()
    await page.getByRole('heading', { name: 'Prompt revenue', exact: true }).waitFor()
    await page.getByRole('button', { name: 'Expand', exact: true }).click()
    await page.locator('.report-widget [title="1980.00"]').waitFor()
    await page.getByRole('button', { name: 'Send', exact: true }).waitFor()
    await page.reload()
    await page.getByRole('heading', { name: 'Prompt revenue', exact: true }).waitFor()
    await page.getByRole('button', { name: 'Expand', exact: true }).click()
    await page.locator('.report-widget [title="1980.00"]').waitFor()
    await page.getByRole('button', { name: 'Open full', exact: true }).click()
    await page.waitForURL(url => url.pathname.startsWith('/studio/reports/'))
    await page.getByRole('heading', { name: 'Prompt revenue', exact: true }).waitFor()
    await page.locator('.report-widget [title="1980.00"]').waitFor()
    const reportId = new URL(page.url()).pathname.split('/').at(-1)
    await page.screenshot({ path: input.screenshot, fullPage: true, animations: 'disabled' })
    console.log(JSON.stringify({ status: 'passed', journey: 'scripted-provider-real-http-consent-report-stream', report_id: reportId }))
  } else {
  const reportPath = `/api/v1/agents/studio/reports/${input.report_id}`
  async function get(suffix) {
    const response = await fetch(origin + reportPath + suffix, { headers: { Authorization: `Bearer ${input.token}` } })
    assert.equal(response.status, 200, 'Authenticated report read failed')
    return response.json()
  }
  const original = await get('/revisions/1')
  const chat = `${origin}/studio?agent=${input.origin.agent_id}&thread=${input.origin.thread_id}`
  await page.goto(chat)
  await page.getByRole('button', { name: 'Expand', exact: true }).click()
  assert.equal(await page.getByRole('combobox', { name: 'City', exact: true }).inputValue(), '"Jakarta"')
  await page.locator('.report-widget [title="1980.00"]').waitFor()
  await page.getByRole('button', { name: 'Open full', exact: true }).focus()
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: 'Edit draft', exact: true }).waitFor()
  await page.getByRole('combobox', { name: 'City', exact: true }).selectOption('"Bandung"')
  await page.locator('.report-widget [title="3960.00"]').waitFor()
  assert.equal(new URL(page.url()).searchParams.has('handoff'), false)
  await page.screenshot({ path: input.screenshot.replace('.png', '-bandung.png'), fullPage: true, animations: 'disabled' })
  const attached = page.waitForResponse(response => response.url().endsWith(reportPath + '/context'))
  await page.getByRole('button', { name: 'Ask Nova', exact: true }).click()
  const contextResponse = await attached
  assert.equal(contextResponse.status(), 200)
  const contextReference = (await contextResponse.json()).reference
  await page.getByRole('button', { name: 'Expand', exact: true }).click()
  await page.locator('.report-widget [title="3960.00"]').waitFor()
  assert.equal(await page.getByRole('combobox', { name: 'City', exact: true }).inputValue(), '"Bandung"')
  await page.reload()
  await page.getByRole('button', { name: 'Expand', exact: true }).click()
  await page.locator('.report-widget [title="3960.00"]').waitFor()
  await page.goto(`${origin}/studio/reports/${input.report_id}`)
  await page.getByRole('heading', { name: 'Browser revenue', exact: true }).waitFor()
  await page.locator('.report-widget [title="1980.00"]').waitFor()
  await page.getByRole('button', { name: 'Edit draft', exact: true }).click()
  await page.getByLabel('Title', { exact: true }).fill('Browser revised revenue')
  await page.getByRole('button', { name: 'Review changes', exact: true }).click()
  await page.getByRole('button', { name: 'Preview data', exact: true }).click()
  await page.getByRole('dialog').getByRole('heading', { name: 'Browser revised revenue', exact: true }).waitFor()
  await page.getByRole('dialog').locator('.report-widget [title="1980.00"]').waitFor()
  assert.deepEqual(await get('/revisions/1'), original)
  await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click()
  await page.getByRole('button', { name: 'Apply to draft', exact: true }).click()
  await page.getByRole('heading', { name: 'Browser revised revenue', exact: true }).waitFor()
  assert.equal((await get('')).published_revision, 1)
  await page.getByRole('button', { name: 'Publish', exact: true }).click()
  await page.getByRole('button', { name: 'Publish report', exact: true }).click()
  await page.getByRole('button', { name: 'Edit draft', exact: true }).waitFor()
  assert.equal((await get('')).published_revision, 2)
  assert.deepEqual(await get('/revisions/1'), original)
  await page.reload()
  await page.getByRole('heading', { name: 'Browser revised revenue', exact: true }).waitFor()
  await page.locator('.report-widget [title="1980.00"]').waitFor()
  await page.getByRole('button', { name: 'City comparison', exact: true }).click()
  await page.getByRole('heading', { name: 'Revenue by city', exact: true }).waitFor()
  await page.locator('.report-widget canvas').waitFor()
  await page.screenshot({ path: input.screenshot.replace('.png', '-chart.png'), fullPage: true, animations: 'disabled' })
  await page.getByText('View data and select values', { exact: true }).click()
  await page.getByRole('combobox', { name: 'Select a value from Revenue by city' }).selectOption('"Jakarta"')
  await page.getByRole('button', { name: 'Clear selection', exact: true }).click()
  await page.getByRole('button', { name: 'Clear selection', exact: true }).waitFor({ state: 'hidden' })
  await page.getByText('Select values to explore', { exact: true }).click()
  await page.getByRole('checkbox', { name: 'Jakarta', exact: true }).check()
  await page.getByRole('button', { name: 'Drill into selection', exact: true }).click()
  await page.getByLabel('Drill path', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Back one level', exact: true }).click()
  await page.getByLabel('Drill path', { exact: true }).waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: 'Overview', exact: true }).click()
  await page.locator('.report-widget [title="1980.00"]').waitFor()
  await page.getByRole('button', { name: 'More actions', exact: true }).click()
  for (const format of ['xlsx', 'pdf', 'pptx']) {
    await page.getByRole('combobox', { name: 'Report export format' }).selectOption(format)
    const prepared = page.waitForResponse(r => r.url().endsWith(`${reportPath}/exports`) && r.request().method() === 'POST', { timeout: 120000 })
    const delivery = page.waitForResponse(r => r.url().includes(`${reportPath}/exports/`) && r.url().endsWith('/download'), { timeout: 120000 })
    const downloaded = page.waitForEvent('download', { timeout: 120000 }).catch(() => null)
    await page.getByRole('button', { name: 'Export', exact: true }).click()
    const response = await prepared
    assert.equal(response.status(), 200, `Export ${format}: ${(await response.json()).detail ?? 'Failed'}`)
    const downloadResponse = await delivery
    assert.equal(downloadResponse.status(), 200, `Download ${format} returned ${downloadResponse.status()}`)
    const file = await downloaded
    assert.ok(file, `Export ${format} download missing`)
    assert.equal(file.suggestedFilename(), `report.${format}`)
    await file.saveAs(input.screenshot.replace('.png', `.${format}`))
  }
  await page.getByRole('button', { name: 'More actions', exact: true }).click()
  await page.getByRole('button', { name: 'Export', exact: true }).waitFor({ state: 'hidden' })
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => {
      document.documentElement.classList.remove('light', 'dark')
      document.documentElement.classList.add(theme)
    }, theme)
    for (const width of [320, 390, 768, 1280]) {
      await page.setViewportSize({ width, height: 900 })
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))) })
      await page.getByRole('button', { name: 'More actions', exact: true }).waitFor()
      await page.waitForFunction(() => [...document.querySelectorAll('.smart-report header button')].every(button => { const r = button.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth + 1 }))
      await page.getByRole('heading', { name: 'Browser revised revenue', exact: true }).waitFor()
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Horizontal overflow: ${theme} ${width}`)
      await page.screenshot({ path: input.screenshot.replace('.png', `-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
    }
  }
  await page.setViewportSize({ width: 640, height: 450 })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Report overflows at 200% zoom')
  await page.getByRole('button', { name: 'More actions', exact: true }).focus()
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: 'Activity & schedules', exact: true }).waitFor()
  await page.waitForFunction(() => document.activeElement?.textContent === 'Save template')
  await page.getByRole('button', { name: 'Save template', exact: true }).press('Escape')
  await page.getByRole('button', { name: 'Activity & schedules', exact: true }).waitFor({ state: 'hidden' })
  assert.ok((await page.getByRole('textbox', { name: 'Search City' }).boundingBox()).width >= 100, 'Filter entry must remain usable at zoom reflow')
  await page.screenshot({ path: input.screenshot.replace('.png', '-zoom-200.png'), fullPage: true, animations: 'disabled' })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.screenshot({ path: input.screenshot, fullPage: true, animations: 'disabled' })
  await page.goto(`${origin}/studio?agent=${input.origin.agent_id}&thread=${input.ui_thread_id}`)
  await page.getByText('Verified city data', { exact: true }).waitFor()
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => { document.documentElement.classList.remove('light', 'dark'); document.documentElement.classList.add(theme) }, theme)
    for (const width of [390, 1280]) {
      await page.setViewportSize({ width, height: 900 })
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Intelligence UI overflow: ${theme} ${width}`)
      await page.screenshot({ path: input.screenshot.replace('.png', `-intelligence-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
      if (width === 390) {
        const city = page.getByRole('cell', { name: 'Jakarta', exact: true })
        await city.scrollIntoViewIfNeeded()
        const cell = await city.boundingBox(), composer = await page.getByRole('textbox', { name: 'Message Nova' }).boundingBox()
        assert.ok(cell && composer && cell.y + cell.height <= composer.y, 'Mobile table rows must scroll above the composer')
        await page.screenshot({ path: input.screenshot.replace('.png', `-intelligence-${theme}-390-scrolled.png`), fullPage: true, animations: 'disabled' })
      }
    }
  }
  await page.getByText('Verified city data', { exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: input.screenshot.replace('.png', '-intelligence-data.png'), fullPage: true, animations: 'disabled' })
  await page.goto(`${origin}/studio?view=dashboards`)
  await page.getByRole('heading', { name: 'Reports', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Legacy dashboards', exact: true }).click()
  await page.getByRole('heading', { name: 'Dashboards', exact: true }).waitFor()
  await page.getByRole('textbox', { name: 'New dashboard name' }).waitFor()
  await page.goto(`${origin}/studio?view=artifacts`)
  await page.getByRole('heading', { name: 'Legacy city table', exact: true }).click()
  await page.getByRole('button', { name: 'Add to report', exact: true }).click()
  await page.getByRole('combobox', { name: 'Destination report' }).selectOption(input.report_id)
  await page.getByRole('button', { name: 'Add to draft', exact: true }).click()
  await page.getByText('Requires mapping', { exact: true }).waitFor()
  assert.equal((await get('')).published_revision, 2, 'Adding an artifact must preserve the published revision')
  assert.equal((await get('/revisions/2')).spec.pages.flatMap(p => p.sections.flatMap(s => s.widgets)).length, 2)
  await page.screenshot({ path: input.screenshot.replace('.png', '-artifact-mapping.png'), fullPage: true, animations: 'disabled' })
  console.log(JSON.stringify({ status: 'passed', journey: 'live-governed-chat-handoff-editor-publish', layouts: 8, context_reference: contextReference }))
  }
} catch (error) {
  if (page) await page.screenshot({ path: input.screenshot, fullPage: true, animations: 'disabled' }).catch(() => {})
  console.error(error.name + ': ' + error.message)
  process.exitCode = 1
} finally {
  await browser?.close()
  await vite.close()
}
