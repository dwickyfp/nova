import type { EChartsCoreOption } from 'echarts/core'
import type { ChartDefinition, DatasetQueryResult } from '../contracts'
import { cellLabel, cellNumber, columnLabel, formatNumber, hasSplit, humanize, periodEnd } from './chart-query'
import type { VizTokens } from './echart-host'

type Result = DatasetQueryResult
type Style = ChartDefinition['style']

function palette(tokens: VizTokens, style: Style | undefined, count: number): string[] {
  const kind = style?.palette ?? 'neutral'
  if (kind === 'single_tone') return tokens.tones
  if (kind === 'neutral' && count <= 1) return [tokens.mark]
  return tokens.series
}

/** The most periods a time axis is filled to; a wider range keeps only returned periods. */
const MAX_FILLED_PERIODS = 1000

type Row = Result['rows'][number]

/**
 * Every period from the first to the last returned one, so a missing month is a
 * gap on the axis rather than vanishing. Only for one ascending date dimension.
 */
function filledPeriods(rows: readonly Row[], column: Result['columns'][number] | undefined): Row[number][] | null {
  const grain = column?.date_grain
  const first = rows[0]?.[0]
  if (!grain || !first || (first.type !== 'date' && first.type !== 'timestamp')) return null
  const starts: string[] = []
  for (const row of rows) {
    const cell = row[0]
    if (!cell || (cell.type !== 'date' && cell.type !== 'timestamp')) return null
    const start = cell.value.slice(0, 10)
    if (starts.length && start < starts[starts.length - 1]) return null
    if (starts[starts.length - 1] !== start) starts.push(start)
  }
  const cells: Row[number][] = []
  for (let start = starts[0]; start <= starts[starts.length - 1]; start = periodEnd(start, grain)) {
    if (cells.length >= MAX_FILLED_PERIODS) return null
    cells.push({ ...first, value: first.type === 'timestamp' ? `${start}${first.value.slice(10)}` : start } as Row[number])
  }
  return cells
}

/** Small stable hash, so a series keeps its colour whatever else is filtered out. */
function hash(text: string): number {
  let value = 2166136261
  for (let i = 0; i < text.length; i++) value = Math.imul(value ^ text.charCodeAt(i), 16777619)
  return value >>> 0
}

/** A colour per series name; collisions take the next free colour in name order. */
function stableColors(names: readonly string[], colors: readonly string[]): string[] {
  const taken = new Map<number, string>()
  const chosen = new Map<string, string>()
  for (const name of [...names].sort()) {
    let slot = hash(name) % colors.length
    for (let tries = 0; taken.has(slot) && tries < colors.length; tries++) slot = (slot + 1) % colors.length
    taken.set(slot, name)
    chosen.set(name, colors[slot])
  }
  return names.map(name => chosen.get(name)!)
}

function axisBase(tokens: VizTokens) {
  return {
    axisLine: { lineStyle: { color: tokens.axis } },
    axisTick: { show: false },
    axisLabel: { color: tokens.muted, hideOverlap: true },
    splitLine: { lineStyle: { color: tokens.grid } },
    nameTextStyle: { color: tokens.muted },
  }
}

function common(tokens: VizTokens, style: Style | undefined, legend: boolean) {
  return {
    animationDuration: 300,
    textStyle: { color: tokens.text, fontFamily: 'inherit' },
    tooltip: { trigger: 'item', confine: true, valueFormatter: (value: unknown) =>
      typeof value === 'number' ? formatNumber(value) : String(value ?? '') },
    legend: legend && style?.show_legend !== false
      ? { type: 'scroll', bottom: 0, textStyle: { color: tokens.muted }, icon: 'roundRect', itemWidth: 10, itemHeight: 10 }
      : { show: false },
  }
}

/** Build one ECharts option from a governed query result. Values are never invented. */
export function buildChartOption(chart: ChartDefinition, result: Result, tokens: VizTokens,
  selected: string | readonly string[] | null = null, width?: number): EChartsCoreOption {
  // One or more selected labels: the rest are dimmed.
  const chosen = selected === null ? null : new Set(typeof selected === 'string' ? [selected] : selected)
  if (chosen && !chosen.size) return buildChartOption(chart, result, tokens, null, width)
  const columns = result.columns
  const rows = result.rows
  const style = chart.style
  const encoding = chart.encoding
  const labels = style?.show_labels === true
  const axisTitles = style?.show_axis_titles !== false

  if (encoding.kind === 'cartesian') {
    const split = hasSplit(encoding)
    const dimensionCount = encoding.dimensions.length + (split ? 1 : 0)
    const colorIndex = split ? dimensionCount - 1 : -1
    const categoryColumn = columns[0]
    const categories: string[] = []
    const seen = new Map<string, number>()
    const periods = encoding.dimensions.length === 1 ? filledPeriods(rows, categoryColumn) : null
    for (const cell of periods ?? []) {
      const key = cellLabel(cell, categoryColumn)
      if (!seen.has(key)) { seen.set(key, categories.length); categories.push(key) }
    }
    for (const row of rows) {
      const key = encoding.dimensions.map((_, i) => cellLabel(row[i], columns[i])).join(' · ')
      if (!seen.has(key)) { seen.set(key, categories.length); categories.push(key) }
    }
    const measureColumns = columns.slice(dimensionCount)
    type Series = { name: string; data: (number | null)[]; type: string; axis: number; area: boolean }
    const series: Series[] = []
    if (colorIndex >= 0) {
      const groups = new Map<string, Series>()
      for (const row of rows) {
        const group = cellLabel(row[colorIndex], columns[colorIndex])
        let item = groups.get(group)
        if (!item) {
          const kind = encoding.measures[0]?.series ?? 'bar'
          item = { name: group, data: categories.map(() => null), type: chart.visual === 'line' ? 'line' : chart.visual === 'area' ? 'line' : kind === 'bar' ? 'bar' : 'line',
            axis: 0, area: chart.visual === 'area' || kind === 'area' }
          groups.set(group, item)
        }
        const key = encoding.dimensions.map((_, i) => cellLabel(row[i], columns[i])).join(' · ')
        item.data[seen.get(key)!] = cellNumber(row[dimensionCount])
      }
      series.push(...groups.values())
    } else {
      measureColumns.forEach((column, index) => {
        const declared = encoding.measures[index]
        const kind = chart.visual === 'combo' ? declared?.series ?? 'bar'
          : chart.visual === 'line' ? 'line' : chart.visual === 'area' ? 'area' : 'bar'
        const data = categories.map(() => null as number | null)
        for (const row of rows) {
          const key = encoding.dimensions.map((_, i) => cellLabel(row[i], columns[i])).join(' · ')
          data[seen.get(key)!] = cellNumber(row[dimensionCount + index])
        }
        series.push({ name: columnLabel(column),
          data, type: kind === 'bar' ? 'bar' : 'line', axis: declared?.axis === 'right' ? 1 : 0, area: kind === 'area' })
      })
    }
    if (encoding.stack === 'percent') {
      categories.forEach((_, i) => {
        const total = series.reduce((sum, item) => sum + Math.abs(item.data[i] ?? 0), 0)
        series.forEach(item => { if (item.data[i] !== null && total) item.data[i] = (item.data[i]! / total) * 100 })
      })
    }
    if (encoding.nulls === 'zero') series.forEach(item => { item.data = item.data.map(value => value ?? 0) })
    const base = palette(tokens, style, series.length)
    // A split series is coloured by its name, so filtering others never repaints it.
    const colors = colorIndex >= 0 && base.length > 1 ? stableColors(series.map(item => item.name), base) : base
    const horizontal = encoding.orientation === 'horizontal'
    const rotated = !horizontal && categories.length > 8
    // A category axis title would collide with rotated labels, or with the labels
    // of a horizontal bar's vertical axis; the chart title names it instead.
    const categoryAxis = { ...axisBase(tokens), type: 'category', data: categories,
      name: axisTitles && !rotated && !horizontal && categoryColumn ? humanize(categoryColumn.name) : undefined,
      nameLocation: 'middle', nameGap: 28,
      splitLine: { show: false }, axisLabel: { ...axisBase(tokens).axisLabel, rotate: rotated ? 30 : 0,
        // A few categories keep every label, shortened to its band, rather than dropping some.
        ...(!horizontal && !rotated && width && categories.length
          ? { interval: 0, hideOverlap: false, overflow: 'truncate', width: Math.max(32, Math.floor((width - 56) / categories.length) - 4) }
          : {}) } }
    const valueAxis = (index: number) => ({ ...axisBase(tokens), type: 'value', scale: false,
      name: axisTitles ? (index === 0 ? chart.left_axis?.title : chart.right_axis?.title) ?? undefined : undefined,
      axisLabel: { ...axisBase(tokens).axisLabel, formatter: (value: number) => encoding.stack === 'percent' ? `${value}%` : formatNumber(value, 'compact') } })
    const hasRight = series.some(item => item.axis === 1)
    const valueAxes = hasRight ? [valueAxis(0), { ...valueAxis(1), splitLine: { show: false } }] : [valueAxis(0)]
    const bySeries = chosen !== null && series.some(item => chosen.has(item.name))
    return {
      ...common(tokens, style, series.length > 1),
      color: colors,
      tooltip: { trigger: 'axis', confine: true, axisPointer: { type: horizontal ? 'shadow' : 'line' },
        valueFormatter: (value: unknown) => typeof value === 'number' ? formatNumber(value) : '–' },
      grid: { left: 8, right: 16, top: 24, bottom: (series.length > 1 ? 36 : 8) + (categoryAxis.name && !horizontal ? 20 : 0), outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
      xAxis: horizontal ? valueAxes : categoryAxis,
      yAxis: horizontal ? categoryAxis : valueAxes,
      dataZoom: categories.length > 60 ? [{ type: 'inside' }] : undefined,
      series: series.map(item => ({
        name: item.name, type: item.type,
        // A legend selection picks whole series; a mark selection picks categories.
        data: chosen === null || bySeries ? item.data : item.data.map((value, index) => ({
          value, itemStyle: { opacity: chosen.has(categories[index]) ? 1 : 0.3 },
        })),
        ...(bySeries ? { itemStyle: { ...(item.type === 'bar' ? { borderRadius: horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0] } : {}),
          opacity: chosen!.has(item.name) ? 1 : 0.3 }, lineStyle: { opacity: chosen!.has(item.name) ? 1 : 0.3 } } : {}),
        stack: encoding.stack && encoding.stack !== 'none' ? 'total' : undefined,
        [horizontal ? 'xAxisIndex' : 'yAxisIndex']: item.axis,
        smooth: encoding.interpolation === 'smooth', step: encoding.interpolation === 'step' ? 'middle' : undefined,
        connectNulls: encoding.nulls === 'connect',
        areaStyle: item.area ? { opacity: 0.18 } : undefined,
        showSymbol: categories.length <= 40,
        barMaxWidth: 48,
        itemStyle: item.type === 'bar' ? { borderRadius: horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0] } : undefined,
        label: { show: labels, position: horizontal ? 'right' : 'top', color: tokens.muted,
          formatter: (params: { value: number }) => formatNumber(params.value, 'compact') },
        emphasis: { focus: 'series' },
      })),
    }
  }

  if (encoding.kind === 'category') {
    const data = rows.map(row => {
      const name = cellLabel(row[0], columns[0])
      return { name, value: cellNumber(row[1]), ...(chosen !== null ? { itemStyle: { opacity: chosen.has(name) ? 1 : 0.3 } } : {}) }
    })
    const funnel = chart.visual === 'funnel'
    return {
      ...common(tokens, style, true),
      color: tokens.series,
      series: [funnel
        ? { type: 'funnel', data, left: 8, right: 8, top: 8, bottom: 32, sort: 'descending',
          label: { show: true, color: tokens.text, formatter: '{b}' } }
        : { type: 'pie', data, radius: encoding.donut ? ['48%', '72%'] : ['0%', '72%'], center: ['50%', '45%'],
          itemStyle: { borderColor: tokens.surface, borderWidth: 2 },
          label: { show: labels, color: tokens.text, formatter: '{b}: {d}%' }, avoidLabelOverlap: true }],
    }
  }

  if (encoding.kind === 'scatter' || encoding.kind === 'map') {
    const sized = encoding.kind === 'scatter' && Boolean(encoding.size)
    const colorIndex = encoding.kind === 'scatter' ? (encoding.color ? (sized ? 3 : 2) : -1)
      : encoding.kind === 'map' && encoding.group ? 2 : -1
    const groups = new Map<string, number[][]>()
    for (const row of rows) {
      const point = encoding.kind === 'map'
        ? [cellNumber(row[1]) ?? 0, cellNumber(row[0]) ?? 0]
        : [cellNumber(row[0]) ?? 0, cellNumber(row[1]) ?? 0, sized ? cellNumber(row[2]) ?? 0 : 0]
      const group = colorIndex >= 0 ? cellLabel(row[colorIndex], columns[colorIndex]) : 'Points'
      groups.set(group, [...(groups.get(group) ?? []), point])
    }
    const sizes = [...groups.values()].flat().map(point => point[2] ?? 0)
    const maxSize = Math.max(1, ...sizes)
    return {
      ...common(tokens, style, groups.size > 1),
      color: palette(tokens, style, groups.size),
      grid: { left: 8, right: 16, top: 16, bottom: groups.size > 1 ? 36 : 8, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
      xAxis: { ...axisBase(tokens), type: 'value', scale: true, name: axisTitles ? humanize(columns[encoding.kind === 'map' ? 1 : 0]?.name ?? '') : undefined, nameLocation: 'middle', nameGap: 28 },
      yAxis: { ...axisBase(tokens), type: 'value', scale: true, name: axisTitles ? humanize(columns[encoding.kind === 'map' ? 0 : 1]?.name ?? '') : undefined },
      series: [...groups.entries()].map(([name, data]) => ({
        type: 'scatter', name, data,
        symbolSize: sized ? (point: number[]) => 6 + 24 * Math.sqrt((point[2] ?? 0) / maxSize) : 8,
        itemStyle: { opacity: 0.8 },
      })),
    }
  }

  if (encoding.kind === 'heatmap') {
    const xs: string[] = []
    const ys: string[] = []
    const data: [number, number, number | null][] = []
    for (const row of rows) {
      const x = cellLabel(row[0], columns[0])
      const y = encoding.y ? cellLabel(row[1], columns[1]) : columns[columns.length - 1].name
      if (!xs.includes(x)) xs.push(x)
      if (!ys.includes(y)) ys.push(y)
      data.push([xs.indexOf(x), ys.indexOf(y), cellNumber(row[row.length - 1])])
    }
    const values = data.map(item => item[2] ?? 0)
    return {
      ...common(tokens, style, false),
      tooltip: { position: 'top', confine: true },
      grid: { left: 8, right: 16, top: 8, bottom: 48, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
      xAxis: { ...axisBase(tokens), type: 'category', data: xs, splitArea: { show: false } },
      yAxis: { ...axisBase(tokens), type: 'category', data: ys },
      visualMap: { min: Math.min(0, ...values), max: Math.max(1, ...values), calculable: true, orient: 'horizontal',
        left: 'center', bottom: 0, itemHeight: 120, textStyle: { color: tokens.muted },
        inRange: { color: [...tokens.tones].reverse() } },
      series: [{ type: 'heatmap', data, label: { show: labels, color: tokens.text } }],
    }
  }

  if (encoding.kind === 'distribution') {
    const points = rows.map(row => ({ value: cellNumber(row[0]), count: cellNumber(row[row.length - 1]) ?? 0 }))
      .filter((point): point is { value: number; count: number } => point.value !== null)
    if (chart.visual === 'boxplot') {
      const sorted = points.sort((a, b) => a.value - b.value)
      const total = sorted.reduce((sum, point) => sum + point.count, 0)
      const quantile = (q: number) => {
        let seen = 0
        for (const point of sorted) { seen += point.count; if (seen >= q * total) return point.value }
        return sorted[sorted.length - 1]?.value ?? 0
      }
      return {
        ...common(tokens, style, false), color: [tokens.mark],
        grid: { left: 8, right: 16, top: 16, bottom: 8, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
        xAxis: { ...axisBase(tokens), type: 'category', data: [columns[0]?.name ?? ''] },
        yAxis: { ...axisBase(tokens), type: 'value', scale: true },
        series: [{ type: 'boxplot', data: total ? [[quantile(0), quantile(0.25), quantile(0.5), quantile(0.75), quantile(1)]] : [] }],
      }
    }
    const bins = encoding.bins ?? 20
    const min = Math.min(...points.map(point => point.value))
    const max = Math.max(...points.map(point => point.value))
    const width = points.length && max > min ? (max - min) / bins : 1
    const counts = Array.from({ length: points.length ? bins : 0 }, () => 0)
    for (const point of points) counts[Math.min(bins - 1, Math.floor((point.value - min) / width))] += point.count
    return {
      ...common(tokens, style, false), color: [tokens.mark],
      tooltip: { trigger: 'axis', confine: true },
      grid: { left: 8, right: 16, top: 16, bottom: 8, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
      xAxis: { ...axisBase(tokens), type: 'category', data: counts.map((_, i) => `${formatNumber(min + i * width, 'compact')}–${formatNumber(min + (i + 1) * width, 'compact')}`) },
      yAxis: { ...axisBase(tokens), type: 'value', name: axisTitles ? 'Rows' : undefined },
      series: [{ type: 'bar', data: counts, barCategoryGap: '2%' }],
    }
  }

  if (encoding.kind === 'hierarchy') {
    type Node = { name: string; value: number; children?: Node[] }
    const root: Node[] = []
    const levels = encoding.levels.length
    for (const row of rows) {
      let siblings = root
      for (let level = 0; level < levels; level++) {
        const name = cellLabel(row[level], columns[level])
        let node = siblings.find(item => item.name === name)
        if (!node) { node = { name, value: 0, ...(level < levels - 1 ? { children: [] } : {}) }; siblings.push(node) }
        node.value += cellNumber(row[levels]) ?? 0
        siblings = node.children ?? []
      }
    }
    return {
      ...common(tokens, style, false), color: tokens.series,
      series: [{ type: 'treemap', data: root, roam: false, nodeClick: false, breadcrumb: { show: levels > 1 },
        label: { show: true, color: tokens.surface }, upperLabel: { show: levels > 1, color: tokens.text },
        itemStyle: { borderColor: tokens.surface, borderWidth: 1, gapWidth: 1 } }],
    }
  }

  if (encoding.kind === 'flow') {
    const names = new Set<string>()
    const links = rows.map(row => {
      const source = cellLabel(row[0], columns[0])
      const target = `${cellLabel(row[1], columns[1])} `
      names.add(source); names.add(target)
      return { source, target, value: cellNumber(row[2]) ?? 0 }
    })
    return {
      ...common(tokens, style, false), color: tokens.series,
      series: [{ type: 'sankey', data: [...names].map(name => ({ name })), links, emphasis: { focus: 'adjacency' },
        label: { color: tokens.text }, lineStyle: { color: 'gradient', opacity: 0.35 } }],
    }
  }

  if (encoding.kind === 'single_measure' && chart.visual === 'gauge') {
    const value = cellNumber(rows[0]?.[0] ?? null) ?? 0
    const target = encoding.target ? Number(encoding.target) : Math.max(value * 1.25, 1)
    return {
      series: [{ type: 'gauge', min: 0, max: target, progress: { show: true, width: 12 },
        axisLine: { lineStyle: { width: 12, color: [[1, tokens.grid]] } }, itemStyle: { color: tokens.series[0] },
        pointer: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
        detail: { valueAnimation: true, color: tokens.text, fontSize: 24, formatter: (v: number) => formatNumber(v, 'compact') },
        data: [{ value }] }],
    }
  }
  throw new Error('This visualization is rendered without a chart canvas')
}
