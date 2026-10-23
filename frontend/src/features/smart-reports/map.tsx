import { useState } from 'react'
import { MapMarker, MarkerContent } from '@/components/ui/map'
import { MapSurface } from '@/features/studio/intelligent-ui/map-surface'
import type { Scalar, Widget, WidgetResult } from './contracts'

export function ReportMap({ widget, result, onSelect }: { widget: Widget; result: WidgetResult; onSelect?: (value: Scalar) => void }) {
  const [failed, setFailed] = useState(false)
  const [ready, setReady] = useState(false)
  const index = (field?: string | null) => result.columns.findIndex(c => c === field || c === field?.replace(/\./g, '__'))
  const points = result.rows.flatMap((row, i) => {
    const latitude = row[index(widget.encoding.latitude)], longitude = row[index(widget.encoding.longitude)]
    if (latitude == null || longitude == null || !Number.isFinite(Number(latitude)) || !Number.isFinite(Number(longitude)) || Math.abs(Number(latitude)) > 90 || Math.abs(Number(longitude)) > 180) return []
    const label = row[index(widget.encoding.x)] ?? `Location ${i + 1}`
    return [{ i, latitude: Number(latitude), longitude: Number(longitude), label, value: row[index(widget.encoding.value ?? widget.query?.metrics[0])] }]
  })
  const bounds: [[number, number], [number, number]] | null = points.length ? [[Math.min(...points.map(p => p.longitude)), Math.min(...points.map(p => p.latitude))], [Math.max(...points.map(p => p.longitude)), Math.max(...points.map(p => p.latitude))]] : null
  if (failed) return <p data-report-visual="failed" role="status" className="text-sm text-muted-foreground">Map unavailable. All governed values remain available in the data table.</p>
  return <div className="space-y-2" data-report-visual={ready ? 'ready' : 'loading'}>
    {points.length !== result.rows.length ? <p role="status" className="text-xs text-muted-foreground">{result.rows.length - points.length} rows have missing or invalid coordinates.</p> : null}
    <MapSurface basemap="bundled" options={{ controls: !!onSelect }} onReady={() => setReady(true)} bounds={bounds} onError={() => setFailed(true)}>{points.map(point => <MapMarker key={point.i} latitude={point.latitude} longitude={point.longitude}><MarkerContent><button type="button" disabled={!onSelect} onClick={() => onSelect?.(point.label)} aria-label={`${point.label}: ${point.value ?? 'No value'}`} title={`${point.label}: ${point.value ?? '—'}`} className="size-5 rounded-full border-2 border-background bg-primary focus-visible:ring-2 focus-visible:ring-ring" /></MarkerContent></MapMarker>)}</MapSurface>
  </div>
}
