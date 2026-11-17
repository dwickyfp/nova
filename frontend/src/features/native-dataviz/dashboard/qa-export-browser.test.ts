import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ReportItem } from './export'
import { printHtml, reportHtml } from './export'

// Release QA NDV-PERF-010 (docs/reports/native-dataviz/QA_MATRIX.csv). A PDF is the
// browser's own print of a report built on the person's device: there is no export
// worker or stored file to bound on the server. What can grow is the report, so a
// 20-page dashboard with large tables is built and laid out here with time, size and
// memory limits, and it may load nothing from outside the page.

const PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='

function twentyPages(): ReportItem[] {
  const items: ReportItem[] = []
  for (let page = 1; page <= 20; page += 1) {
    items.push({ kind: 'page', title: `Page ${page}` })
    for (let widget = 1; widget <= 6; widget += 1) {
      const title = `Widget ${page}.${widget}`
      if (widget <= 2) {
        // 2,000 rows each: the report keeps the first 500 and says so.
        const rows = Array.from({ length: 2000 }, (_, index) => [`Region ${index}`, index * 1.5])
        items.push({ kind: 'table', title, table: { headers: ['Region', 'Revenue'], rows } })
      } else if (widget <= 4) {
        items.push({ kind: 'image', title, src: PIXEL, width: 640 })
      } else {
        items.push({ kind: 'kpi', title, value: `${page * widget}` })
      }
    }
  }
  return items
}

afterEach(() => vi.restoreAllMocks())

describe('NDV-PERF-010 a 20-page report', () => {
  it('is built and laid out within bounds, one page per sheet, with nothing loaded from outside', async () => {
    const heap = () => (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize
    const heapBefore = heap()
    const started = performance.now()
    const html = reportHtml({ title: 'Sales', subtitle: '20 pages', items: twentyPages() })
    const built = performance.now() - started
    // 20 pages x 2 tables x 500 rows: big tables are capped, never all 2,000 rows.
    expect((html.match(/<tr>/g) ?? []).length).toBe(20 * 2 * 501)
    expect((html.match(/First 500 of 2000 rows\./g) ?? []).length).toBe(40)
    expect(html.length).toBeLessThan(5_000_000)
    expect(built).toBeLessThan(2000)
    // Every page after the first starts on a new sheet.
    expect((html.match(/<h2 class="page">/g) ?? []).length).toBe(1)
    expect((html.match(/<h2 class="page break">/g) ?? []).length).toBe(19)
    // Private output: images are inline and nothing names another origin.
    const sources = [...html.matchAll(/\b(?:src|href)="([^"]*)"/g)].map(match => match[1])
    expect(sources.length).toBe(20 * 2)
    expect(sources.every(source => source.startsWith('data:image/png;base64,'))).toBe(true)
    expect(html).not.toMatch(/https?:\/\/|<script|<link|@import/i)

    // Laid out in the hidden print frame (the print dialog itself is stubbed).
    const printed = vi.fn()
    let frame: HTMLIFrameElement | null = null
    const append = document.body.append.bind(document.body)
    vi.spyOn(document.body, 'append').mockImplementation((...nodes: (Node | string)[]) => {
      append(...nodes)
      for (const node of nodes) {
        if (node instanceof HTMLIFrameElement && node.contentWindow) {
          frame = node
          node.contentWindow.print = printed
        }
      }
    })
    const laid = performance.now()
    await printHtml(html)
    const layout = performance.now() - laid
    expect(printed).toHaveBeenCalledTimes(1)
    expect(frame).not.toBeNull()
    const printedDoc = (frame as unknown as HTMLIFrameElement).contentDocument!
    expect(printedDoc.querySelectorAll('h2.page').length).toBe(20)
    expect(printedDoc.querySelectorAll('img').length).toBe(40)
    expect(layout).toBeLessThan(5000)
    const heapAfter = heap()
    if (heapBefore !== undefined && heapAfter !== undefined) expect(heapAfter - heapBefore).toBeLessThan(200 * 1024 * 1024)
    // The frame is removed after printing, so nothing stays behind in the page.
    await new Promise(resolve => setTimeout(resolve, 1100))
    expect(document.querySelectorAll('iframe').length).toBe(0)
  })
})
