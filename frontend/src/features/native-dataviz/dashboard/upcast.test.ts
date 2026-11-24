import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import { dashboardSchema, type DashboardDefinitionV2 } from '../contracts'
import schema from '../contracts/schema'
import { cellOf, cellRect, gridWidgets, upcastDashboard, upcastPage } from './layout'

// The server's golden fixtures: the browser must upcast exactly as the server does.
const fixtures = import.meta.glob('../../../../../backend/tests/fixtures/viz_dashboards_v2/*.json',
  { eager: true, import: 'default' }) as Record<string, unknown>
const v2Schema = z.fromJSONSchema({
  $schema: 'https://json-schema.org/draft/2020-12/schema', $ref: '#/$defs/DashboardDefinitionV2',
  $defs: schema.$defs as Record<string, z.core.JSONSchema.JSONSchema>,
}) as z.ZodType<DashboardDefinitionV2>
const cases = Object.keys(fixtures).filter(path => !path.endsWith('.v3.json') && !path.endsWith('legacy-operations.json'))
const byId = <T extends { widget_id: string }>(items: T[]) => [...items].sort((a, b) => a.widget_id.localeCompare(b.widget_id))

describe('v2 dashboards in the browser', () => {
  it('finds every server fixture', () => {
    expect(cases.length).toBeGreaterThanOrEqual(5)
  })

  it.each(cases)('upcasts %s exactly like the server', path => {
    const source = v2Schema.parse(fixtures[path])
    const golden = dashboardSchema.parse(fixtures[path.replace(/\.json$/, '.v3.json')])
    const upcast = dashboardSchema.parse(upcastDashboard(source))
    expect(upcast).toEqual(golden)
    upcast.pages.forEach((page, index) => {
      // The grid projection is the exact inverse: it upcasts back to the same page.
      const widgets = gridWidgets(page)
      expect(byId(widgets).map(item => item.widget_id)).toEqual(byId(source.pages[index].widgets ?? []).map(item => item.widget_id))
      expect(upcastPage({ page_id: page.page_id, title: page.title, widgets })).toEqual(page)
    })
  })

  it('inverts every legacy cell', () => {
    for (let w = 2; w <= 12; w++) {
      for (let x = 0; x + w <= 12; x++) {
        for (const [y, h] of [[0, 2], [7, 48], [10_000, 2]]) {
          expect(cellOf(cellRect({ x, y, w, h }))).toEqual({ x, y, w, h })
        }
      }
    }
  })
})
