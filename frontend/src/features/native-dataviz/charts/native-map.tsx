import { useEffect, useMemo, useRef, useState } from 'react'
import type { GeoJSONSource, MapLayerMouseEvent } from 'maplibre-gl'
import { Scan } from 'lucide-react'
import { Map, MapControlButton, MapControlGroup, MapControlStack, MapPopup, useMap, type MapRef } from '@/components/ui/map'
import { mapColor, motionDuration } from '@/components/ui/map/tokens'
import { useTheme } from '@/context/theme-provider'
import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { PUBLIC_MAP_STYLES } from '../../studio/intelligent-ui/map-style'
import { formatNumber } from './chart-query'
import { mapFeatures, mapModel, type MapModel } from './map-points'

const SOURCE = 'native-map-points'
const LAYER = 'native-map-circles'
// The right side also clears the in-map controls.
const PADDING = { top: 32, right: 48, bottom: 32, left: 32 }
const SERIES = [4, 2, 3, 1, 5, 6, 8, 9, 7]
// More groups than this are named in the point popups instead of the legend.
const LEGEND = 8

/** Map colours as rgb(), since the map cannot read the theme's colour space. */
function colours(chart: ChartDefinition, groups: string[]) {
  const palette = chart.style?.palette ?? 'neutral'
  if (!groups.length) return { single: mapColor(palette === 'single_tone' ? '--chart-tone-4' : '--viz-mark'), byGroup: [] as string[] }
  const names = palette === 'single_tone' ? [1, 2, 3, 4, 5, 6].map(i => `--chart-tone-${i}`) : SERIES.map(i => `--chart-${i}`)
  return { single: mapColor('--viz-mark'), byGroup: groups.map((_, i) => mapColor(names[i % names.length])) }
}

function Points({ model, chart, selected, onPick }: { model: MapModel; chart: ChartDefinition; selected: string[] | null; onPick: (row: number) => void }) {
  const { map, isLoaded } = useMap()
  const { resolvedTheme } = useTheme()
  const touched = useRef(false)
  const pick = useRef(onPick)
  pick.current = onPick
  const data = useMemo(() => mapFeatures(model), [model])

  useEffect(() => {
    if (!map || !isLoaded) return
    const { single, byGroup } = colours(chart, model.groups)
    const color = byGroup.length
      ? ['match', ['get', 'group'], ...model.groups.flatMap((group, i) => [group, byGroup[i]]), single]
      : single
    const radius = model.sized ? ['interpolate', ['linear'], ['get', 'weight'], 0, 4, 1, 22] : 6
    if (!map.getSource(SOURCE)) map.addSource(SOURCE, { type: 'geojson', data })
    else (map.getSource(SOURCE) as GeoJSONSource).setData(data)
    if (map.getLayer(LAYER)) map.removeLayer(LAYER)
    map.addLayer({
      id: LAYER, type: 'circle', source: SOURCE,
      paint: {
        'circle-color': color as never, 'circle-radius': radius as never, 'circle-opacity': (selected === null ? 0.8 : ['case', ['in', ['get', 'group'], ['literal', selected]], 0.9, 0.2]) as never,
        'circle-stroke-color': mapColor('--card'), 'circle-stroke-width': 1,
      },
    })
    const click = (event: MapLayerMouseEvent) => {
      const row = event.features?.[0]?.properties?.row
      if (row !== undefined) pick.current(Number(row))
    }
    const enter = () => { map.getCanvas().style.cursor = 'pointer' }
    const leave = () => { map.getCanvas().style.cursor = '' }
    map.on('click', LAYER, click)
    map.on('mouseenter', LAYER, enter)
    map.on('mouseleave', LAYER, leave)
    return () => {
      map.off('click', LAYER, click)
      map.off('mouseenter', LAYER, enter)
      map.off('mouseleave', LAYER, leave)
    }
  }, [map, isLoaded, data, chart, model.groups, model.sized, selected?.join('\u0000'), resolvedTheme])

  // Credits stay one click away behind the info button instead of covering a small card.
  useEffect(() => {
    if (!map || !isLoaded) return
    map.getContainer().querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show')
  }, [map, isLoaded])

  // Follow the data until someone moves the map, including when the card resizes.
  useEffect(() => {
    if (!map || !isLoaded || !model.bounds) return
    const bounds = model.bounds
    const fit = () => { if (!touched.current) map.fitBounds(bounds, { padding: PADDING, maxZoom: 12, animate: false }) }
    const takeOver = (event: { originalEvent?: unknown }) => { if (event.originalEvent) touched.current = true }
    fit()
    map.on('resize', fit)
    map.on('movestart', takeOver)
    return () => {
      map.off('resize', fit)
      map.off('movestart', takeOver)
    }
  }, [map, isLoaded, model.bounds])

  // Show all returns to the data and follows it again, as on first load.
  const showAll = () => {
    if (!map || !model.bounds) return
    const bounds = model.bounds
    const canvas = map.getCanvas()
    const size = `${canvas.clientWidth}x${canvas.clientHeight}`
    map.once('moveend', () => {
      touched.current = false
      // A resize during the animation would otherwise land on a stale frame.
      if (`${canvas.clientWidth}x${canvas.clientHeight}` !== size) map.fitBounds(bounds, { padding: PADDING, maxZoom: 12, animate: false })
    })
    map.fitBounds(bounds, { padding: PADDING, maxZoom: 12, pitch: 0, bearing: 0, duration: motionDuration(300) })
  }
  return <MapControlStack>
    <MapControlGroup>
      <MapControlButton label='Show all locations' onClick={showAll} disabled={!model.bounds}>
        <Scan className='size-4' aria-hidden />
      </MapControlButton>
    </MapControlGroup>
  </MapControlStack>
}

/** A governed point map: each row placed at its coordinates, sized by its value. */
export function NativeMap({ chart, result, onSelectGroup, selectedLabel = null, label }: {
  chart: ChartDefinition
  result: DatasetQueryResult
  onSelectGroup?: (row: number) => void
  /** The group a cross-filter selected; other groups are dimmed. */
  selectedLabel?: string | readonly string[] | null
  label: string
}) {
  const { resolvedTheme } = useTheme()
  const model = useMemo(() => mapModel(chart, result), [chart, result])
  const [popup, setPopup] = useState<number | null>(null)
  const [failed, setFailed] = useState(false)
  const ref = useRef<MapRef | null>(null)
  const point = model.points.find(item => item.row === popup)
  const legend = chart.style?.show_legend !== false && model.groups.length > 1
  // "and 1 more" says less than the one name it hides.
  const named = model.groups.length > LEGEND + 1 ? LEGEND : model.groups.length
  const { byGroup } = useMemo(() => colours(chart, model.groups), [chart, model.groups, resolvedTheme])

  if (!model.points.length) {
    return <p className='flex flex-1 items-center justify-center p-3 text-center text-sm text-muted-foreground'>
      No rows have a valid latitude and longitude.
    </p>
  }
  return <div className='flex min-h-0 flex-1 flex-col gap-1.5' role='figure' aria-label={label}>
    <div className='relative min-h-0 flex-1 overflow-hidden rounded-md border'>
      {failed
        ? <p className='flex h-full items-center justify-center p-3 text-center text-sm text-muted-foreground'>
          The map could not load. The points are still in this chart's data.
        </p>
        : <Map ref={ref} styles={PUBLIC_MAP_STYLES} theme={resolvedTheme} attributionControl={{ compact: true }}
          // Keeps the drawn map readable for PNG and print exports.
          canvasContextAttributes={{ preserveDrawingBuffer: true }}
          onMapError={() => setFailed(true)}>
          <Points model={model} chart={chart} selected={model.groups.length && selectedLabel !== null && selectedLabel.length ? (typeof selectedLabel === 'string' ? [selectedLabel] : [...selectedLabel]) : null} onPick={row => { setPopup(row); onSelectGroup?.(row) }} />
          {point && <MapPopup longitude={point.longitude} latitude={point.latitude} onClose={() => setPopup(null)} closeButton>
            <div className='space-y-0.5 text-xs'>
              {point.group !== null && <p className='font-medium'>{point.group}</p>}
              <p className='text-muted-foreground'>{model.valueLabel}
                <span className='viz-numerals ml-1.5 text-foreground'>{point.value === null ? '–' : formatNumber(point.value)}</span>
              </p>
            </div>
          </MapPopup>}
        </Map>}
    </div>
    {(legend || model.skipped > 0) && <div className='flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground'>
      {legend && model.groups.slice(0, named).map((group, i) => <span key={group} className='inline-flex min-w-0 items-center gap-1.5'>
        <span aria-hidden className='size-2 shrink-0 rounded-full' style={{ background: byGroup[i] }} /><span className='truncate'>{group}</span>
      </span>)}
      {legend && model.groups.length > named && <span>and {(model.groups.length - named).toLocaleString()} more</span>}
      {model.skipped > 0 && <span className='ml-auto'>
        {model.skipped.toLocaleString()} {model.skipped === 1 ? 'row has' : 'rows have'} no location
      </span>}
    </div>}
  </div>
}
