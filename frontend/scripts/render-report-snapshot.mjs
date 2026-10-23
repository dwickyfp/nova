import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { resolve, extname } from 'node:path'
import { randomBytes } from 'node:crypto'
import { chromium } from 'playwright'

const chunks = []
for await (const chunk of process.stdin) chunks.push(chunk)
const snapshot = JSON.parse(Buffer.concat(chunks).toString('utf8'))
const token = randomBytes(32).toString('hex')
const root = resolve(import.meta.dirname, '../dist')
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.json': 'application/json' }
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost')
  if (url.pathname === '/snapshot-data') {
    if (url.searchParams.get('token') !== token) { res.writeHead(404).end(); return }
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }).end(JSON.stringify(snapshot)); return
  }
  try {
    const file = resolve(root, `.${decodeURIComponent(url.pathname)}`)
    if (!file.startsWith(`${root}/`)) { res.writeHead(404).end(); return }
    const bytes = await readFile(file); res.writeHead(200, { 'Content-Type': types[extname(file)] ?? 'application/octet-stream' }).end(bytes) }
  catch { res.writeHead(404).end() }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 }, reducedMotion: 'reduce' })
  const origin = `http://127.0.0.1:${server.address().port}`
  await page.route('**/*', route => route.request().url().startsWith(`${origin}/`) || route.request().url().startsWith('data:') ? route.continue() : route.abort())
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(`${origin}/report-snapshot.html?token=${token}`)
  await page.waitForFunction(() => document.documentElement.dataset.snapshotReady === 'true', undefined, { timeout: 30000 })
  await page.waitForFunction(() => [...document.querySelectorAll('[data-report-visual]')].every(el => el.getAttribute('data-report-visual') === 'ready'), undefined, { timeout: 30000 })
  await page.evaluate(async () => { await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))) })
  if (errors.length) throw new Error(errors.join('; '))
  const pages = page.locator('[data-snapshot-page]')
  if (await pages.count() !== snapshot.pages.length) throw new Error('Snapshot page count mismatch')
  const images = []
  for (let index = 0; index < await pages.count(); index++) {
    const box = await pages.nth(index).boundingBox()
    const segmentHeight = Math.ceil(box.height / Math.ceil(box.height / 900))
    for (let offset = 0; offset < box.height; offset += segmentHeight) {
      const height = Math.min(segmentHeight, Math.ceil(box.height) - offset)
      const png = await page.screenshot({ clip: { x: box.x, y: box.y + offset, width: box.width, height }, fullPage: true })
      images.push({ page: index, width: box.width, height, png: png.toString('base64') })
    }
  }
  const pdf = await page.pdf({ width: '1200px', height: '900px', printBackground: true, margin: { top: '0', bottom: '0', left: '0', right: '0' } })
  process.stdout.write(JSON.stringify({ pdf: pdf.toString('base64'), images }))
} catch {
  process.stderr.write('Report snapshot rendering failed. Check the built frontend and Chromium runtime.\n')
  process.exitCode = 1
} finally { await browser?.close(); await new Promise(resolve => server.close(resolve)) }
