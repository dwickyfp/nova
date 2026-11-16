import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { cellLabel, cellNumber, columnLabel } from './chart-query'

export type MapPoint = {
  row: number
  latitude: number
  longitude: number
  group: string | null
  value: number | null
  /** 0–1, the square root of value over the largest value, for symbol area; 0 when unsized. */
  weight: number
}

export type MapModel = {
  points: MapPoint[]
  groups: string[]
  /** Rows without a valid coordinate pair; they are counted, never placed. */
  skipped: number
  /** Points are sized by a chosen value; otherwise each is a plain dot. */
  sized: boolean
  valueLabel: string
  groupLabel: string | null
  bounds: [[number, number], [number, number]] | null
}

const valid = (latitude: number | null, longitude: number | null): latitude is number =>
  latitude !== null && longitude !== null && Number.isFinite(latitude) && Number.isFinite(longitude)
  && Math.abs(latitude) <= 90 && Math.abs(longitude) <= 180

/**
 * Points for a map chart from its governed result. The query groups by
 * latitude, longitude and the optional group, then the measure (or a count).
 */
export function mapModel(chart: ChartDefinition, result: DatasetQueryResult): MapModel {
  const encoding = chart.encoding
  const grouped = encoding.kind === 'map' && Boolean(encoding.group)
  const sized = encoding.kind === 'map' && Boolean(encoding.value)
  const valueIndex = grouped ? 3 : 2
  const points: MapPoint[] = []
  let skipped = 0
  result.rows.forEach((row, index) => {
    const latitude = cellNumber(row[0] ?? null)
    const longitude = cellNumber(row[1] ?? null)
    if (!valid(latitude, longitude)) {
      skipped += 1
      return
    }
    points.push({
      row: index, latitude, longitude: longitude!,
      group: grouped ? cellLabel(row[2] ?? null, result.columns[2]) : null,
      value: cellNumber(row[valueIndex] ?? null), weight: 0,
    })
  })
  const largest = Math.max(0, ...points.map(point => Math.abs(point.value ?? 0)))
  if (sized) for (const point of points) point.weight = largest ? Math.sqrt(Math.abs(point.value ?? 0) / largest) : 0
  const groups = grouped ? [...new Set(points.map(point => point.group ?? ''))] : []
  const bounds: MapModel['bounds'] = points.length ? [
    [Math.min(...points.map(point => point.longitude)), Math.min(...points.map(point => point.latitude))],
    [Math.max(...points.map(point => point.longitude)), Math.max(...points.map(point => point.latitude))],
  ] : null
  return {
    points, groups, skipped, bounds, sized,
    // An unsized point counts rows; 'Count of latitude' would name the wrong thing.
    valueLabel: sized && result.columns[valueIndex] ? columnLabel(result.columns[valueIndex]) : 'Rows',
    groupLabel: grouped && result.columns[2] ? columnLabel(result.columns[2]) : null,
  }
}

/** GeoJSON for the map source; feature ids are result rows. */
export function mapFeatures(model: MapModel): GeoJSON.FeatureCollection<GeoJSON.Point> {
  return {
    type: 'FeatureCollection',
    features: model.points.map(point => ({
      type: 'Feature', id: point.row,
      geometry: { type: 'Point', coordinates: [point.longitude, point.latitude] },
      properties: { row: point.row, weight: point.weight, group: point.group ?? '' },
    })),
  }
}
