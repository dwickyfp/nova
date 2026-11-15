import { describe, expect, it } from 'vitest'
import { chartSchema, dashboardSchema, datasetPreviewSchema, operationSchema } from './index'

const id = (last: number) => `00000000-0000-4000-8000-${String(last).padStart(12, '0')}`
const dashboard = {
  dashboard_id: id(1),
  title: 'Sales',
  pages: [{
    page_id: id(2), title: 'Page 1', widgets: [{
      widget_id: id(3), kind: 'chart',
      chart_ref: { chart_id: id(4), revision: 7 },
      layout: { x: 0, y: 0, w: 6, h: 4 },
    }],
  }],
}

describe('canonical native BI wire contracts', () => {
  it('reads a pinned chart reference and canonical desktop layout', () => {
    const result = dashboardSchema.parse(dashboard)
    expect(result.columns).toBe(12)
    expect(result.schema_version).toBe(2)
    expect(result.pages[0].widgets?.[0]).toMatchObject({
      chart_ref: { chart_id: id(4), revision: 7 },
      layout: { x: 0, y: 0, w: 6, h: 4 },
    })
  })

  it.each(['html', 'callback', 'owner_name', 'actor'])('refuses injected %s', (key) => {
    const value = structuredClone(dashboard)
    Object.assign(value.pages[0].widgets[0], { [key]: 'untrusted' })
    expect(dashboardSchema.safeParse(value).success).toBe(false)
  })

  it('rejects unsupported version and a floating-point grid cell', () => {
    expect(dashboardSchema.safeParse({ ...dashboard, schema_version: 3 }).success).toBe(false)
    const value = structuredClone(dashboard)
    value.pages[0].widgets[0].layout.x = 1.5
    expect(dashboardSchema.safeParse(value).success).toBe(false)
  })

  it('accepts semantic operations without allowing authority from the client', () => {
    const operation = {
      operation_id: id(5), expected_revision: 2,
      changes: [{ op: 'dashboard.rename', title: 'Regional sales' }],
    }
    expect(operationSchema.safeParse(operation).success).toBe(true)
    expect(operationSchema.safeParse({ ...operation, actor: 'root' }).success).toBe(false)
  })

  it('rejects executable ECharts options', () => {
    expect(chartSchema.safeParse({
      chart_id: id(4), title: 'Sales', dataset_id: id(6), dataset_revision: 1,
      visual: 'kpi', encoding: {
        kind: 'single_measure', measure: { field_id: 'revenue', aggregation: 'sum' },
      }, options: { tooltip: { formatter: '() => fetch(document.cookie)' } },
    }).success).toBe(false)
  })

  it('roundtrips finite float previews and refuses nonfinite or coerced values', () => {
    const preview = {
      dataset_id: id(6), revision: 1, offset: 0, has_more: false, next_offset: null,
      fields: [{ field_id: 'rate', name: 'Rate', source_column: 'rate', logical_type: 'float',
        physical_type: 'DOUBLE', semantic_role: 'measure', nullable: true }],
      rows: [[{ type: 'float', value: 1.7976931348623157e308 }],
        [{ type: 'float', value: 5e-324 }], [null]],
    }
    expect(datasetPreviewSchema.parse(JSON.parse(JSON.stringify(preview))).rows).toEqual(preview.rows)
    for (const value of [NaN, Infinity, -Infinity, '0.125', true]) {
      expect(datasetPreviewSchema.safeParse({
        ...preview, rows: [[{ type: 'float', value }]],
      }).success).toBe(false)
    }
  })
})
