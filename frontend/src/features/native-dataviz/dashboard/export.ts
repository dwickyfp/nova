import { api } from '@/lib/api-client'
import { readLightToken } from '@/lib/read-token'
import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { cellLabel, columnLabel } from '../charts/chart-query'

export type ChartFileFormat = 'png' | 'csv' | 'xlsx'
export type ExportFormat = ChartFileFormat | 'pdf'

type Value = string | number | boolean | null
export type ResultTable = { headers: string[]; rows: Value[][] }

/** Visuals drawn on a canvas; KPIs and tables are text and have no image. */
export function hasImage(chart: Pick<ChartDefinition, 'visual'>): boolean {
  return chart.visual !== 'kpi' && chart.visual !== 'table'
}

/**
 * Check the Export permission and record the download before the file is built.
 * The file holds only results this person already reads with their role.
 */
export function authorizeExport(dashboardId: string, format: ExportFormat, chartId?: string) {
  return api.post<{ allowed: boolean }>(`/viz/dashboards/${encodeURIComponent(dashboardId)}/exports`,
    { format, ...(chartId ? { chart_id: chartId } : {}) })
}

const NUMERIC = new Set(['integer', 'decimal', 'double', 'float', 'number', 'bigint'])

/** A query result as plain rows: numbers stay numbers, dates keep their grain label. */
export function resultTable(result: DatasetQueryResult): ResultTable {
  const headers = result.columns.map(column => columnLabel(column))
  const rows = result.rows.map(row => row.map((cell, index): Value => {
    if (cell === null) return null
    if (cell.type === 'boolean') return Boolean(cell.value)
    if (cell.type && NUMERIC.has(cell.type)) {
      const number = Number(cell.value)
      // Values beyond exact float range stay text so no digit is lost.
      return Number.isFinite(number) && Math.abs(number) <= Number.MAX_SAFE_INTEGER ? number : String(cell.value)
    }
    return cellLabel(cell, result.columns[index])
  }))
  return { headers, rows }
}

function csvField(value: Value): string {
  if (value === null) return ''
  // Text that a spreadsheet would run as a formula is kept as text (OWASP CSV injection).
  const text = typeof value === 'string' && /^[=+\-@\t\r]/.test(value) ? `'${value}` : String(value)
  return /[",\r\n]/.test(text) || /^\s|\s$/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

/** RFC 4180 CSV with a byte-order mark so spreadsheet apps read UTF-8. */
export function toCsv(table: ResultTable): Blob {
  const lines = [table.headers, ...table.rows].map(row => row.map(csvField).join(','))
  return new Blob(['﻿' + lines.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' })
}

/** Sheet names: at most 31 characters, none of []:*?/\ and unique in the book. */
export function sheetNames(titles: string[]): string[] {
  const used = new Set<string>()
  return titles.map(title => {
    const base = (title.replace(/[[\]:*?/\\]/g, ' ').replace(/\s+/g, ' ').trim() || 'Chart').slice(0, 31).trimEnd()
    let name = base
    for (let n = 2; used.has(name.toLowerCase()); n++) {
      const suffix = ` (${n})`
      name = base.slice(0, 31 - suffix.length).trimEnd() + suffix
    }
    used.add(name.toLowerCase())
    return name
  })
}

/** An Excel workbook with one sheet per table. */
export async function toXlsx(sheets: { title: string; table: ResultTable }[]): Promise<Blob> {
  const XLSX = await import('xlsx')
  const book = XLSX.utils.book_new()
  const names = sheetNames(sheets.map(sheet => sheet.title))
  sheets.forEach((sheet, index) => {
    const worksheet = XLSX.utils.aoa_to_sheet([sheet.table.headers, ...sheet.table.rows])
    worksheet['!cols'] = sheet.table.headers.map((header, column) => ({
      wch: Math.min(48, Math.max(header.length, ...sheet.table.rows.slice(0, 200)
        .map(row => String(row[column] ?? '').length)) + 2),
    }))
    XLSX.utils.book_append_sheet(book, worksheet, names[index])
  })
  const bytes = XLSX.write(book, { type: 'array', bookType: 'xlsx' }) as ArrayBuffer
  return new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
}

/** The chart as drawn, titled, on its card colour, at the screen's pixel density. */
export function chartImage(root: HTMLElement, options: { title: string; background: string; color: string; font: string }): Promise<Blob | null> {
  const source = root.querySelector('canvas')
  if (!source || !source.width || !source.height) return Promise.resolve(null)
  const scale = source.width / Math.max(1, source.getBoundingClientRect().width)
  const pad = Math.round(16 * scale)
  const band = options.title ? Math.round(36 * scale) : 0
  const canvas = document.createElement('canvas')
  canvas.width = source.width + pad * 2
  canvas.height = source.height + band + pad * 2
  const context = canvas.getContext('2d')
  if (!context) return Promise.resolve(null)
  context.fillStyle = options.background
  context.fillRect(0, 0, canvas.width, canvas.height)
  if (options.title) {
    context.fillStyle = options.color
    context.font = `500 ${Math.round(15 * scale)}px ${options.font}`
    context.textBaseline = 'middle'
    context.fillText(options.title, pad, pad + band / 2, canvas.width - pad * 2)
  }
  context.drawImage(source, pad, pad + band)
  return new Promise(resolve => canvas.toBlob(resolve, 'image/png'))
}

/** A safe file name from a title: letters, numbers, spaces and dashes. */
export function fileName(title: string, extension: string): string {
  const base = title.normalize('NFC').replace(/[^\p{L}\p{M}\p{N} _-]+/gu, ' ').replace(/\s+/g, ' ').trim().slice(0, 80)
  return `${base || 'chart'}.${extension}`
}

export function saveFile(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.append(link)
  link.click()
  link.remove()
  // Give the browser time to start the download before the URL is released.
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export type ReportItem =
  | { kind: 'image'; title: string; src: string; width: number }
  | { kind: 'table'; title: string; table: ResultTable }
  | { kind: 'kpi'; title: string; value: string }
  | { kind: 'text'; title: string; text: string }

function escape(text: string): string {
  return text.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!)
}

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })

function tableHtml(table: ResultTable): string {
  const head = table.headers.map(header => `<th>${escape(header)}</th>`).join('')
  const body = table.rows.slice(0, 500).map(row => `<tr>${row.map(value => typeof value === 'number'
    ? `<td class="num">${escape(numberFormat.format(value))}</td>`
    : `<td>${escape(value === null ? '' : String(value))}</td>`).join('')}</tr>`).join('')
  const more = table.rows.length > 500 ? `<p class="note">First 500 of ${table.rows.length} rows.</p>` : ''
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>${more}`
}

/** A printer-friendly page: title, when it was made, then each widget in reading order. */
export function reportHtml(report: { title: string; subtitle: string; items: ReportItem[] }): string {
  const sections = report.items.map(item => {
    const heading = item.title ? `<h2>${escape(item.title)}</h2>` : ''
    const body = item.kind === 'image' ? `<img alt="${escape(item.title)}" src="${item.src}" width="${item.width}">`
      : item.kind === 'table' ? tableHtml(item.table)
        : item.kind === 'kpi' ? `<p class="kpi">${escape(item.value)}</p>`
          : `<p class="text">${escape(item.text)}</p>`
    return `<section>${heading}${body}</section>`
  }).join('')
  // Paper is always the light theme, whatever the screen shows.
  const ink = readLightToken('--foreground', '#1e293b')
  const muted = readLightToken('--muted-foreground', '#5c6e87')
  const line = readLightToken('--border', '#e2e8f0')
  return `<!doctype html><html><head><meta charset="utf-8"><title>${escape(report.title)}</title><style>
@page { margin: 14mm; }
* { box-sizing: border-box; }
body { margin: 0; color: ${ink}; font: 13px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
h1 { margin: 0; font-size: 20px; font-weight: 500; }
.meta { margin: 4px 0 20px; color: ${muted}; font-size: 12px; }
section { margin: 0 0 20px; break-inside: avoid; }
h2 { margin: 0 0 8px; font-size: 14px; font-weight: 500; }
img { display: block; max-width: 100%; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { padding: 4px 8px; border-bottom: 1px solid ${line}; text-align: left; }
th { font-weight: 500; color: ${muted}; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.kpi { margin: 0; font-size: 32px; font-weight: 500; font-variant-numeric: tabular-nums; }
.text { margin: 0; white-space: pre-wrap; }
.note { margin: 4px 0 0; color: ${muted}; font-size: 11px; }
</style></head><body><h1>${escape(report.title)}</h1><p class="meta">${escape(report.subtitle)}</p>${sections}</body></html>`
}

/** Print a page from a hidden frame, so the open dashboard is left as it is. */
export function printHtml(html: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const frame = document.createElement('iframe')
    frame.setAttribute('aria-hidden', 'true')
    frame.style.cssText = 'position:fixed;width:0;height:0;border:0;right:0;bottom:0'
    document.body.append(frame)
    const view = frame.contentWindow
    if (!view) { frame.remove(); reject(new Error('Printing is unavailable')); return }
    view.document.open()
    view.document.write(html)
    view.document.close()
    const images = [...view.document.images]
    void Promise.all(images.map(image => image.complete ? Promise.resolve() : new Promise(done => { image.onload = image.onerror = () => done(undefined) })))
      .then(() => {
        view.focus()
        view.print()
        // The dialog blocks until closed in most browsers; remove the frame afterwards.
        window.setTimeout(() => frame.remove(), 1000)
        resolve()
      })
  })
}
