import type { Scalar, Widget, WidgetResult } from './contracts'

export function chartData(widget: Widget, result: WidgetResult) {
  const x = widget.encoding.x ?? widget.query?.dimensions[0] ?? 'period'
  const ys = widget.encoding.y.length ? widget.encoding.y : widget.query?.metrics ?? []
  const index = (field: string) => result.columns.findIndex(c => c === field || c === field.replace(/\./g, '__'))
  const seriesField = widget.encoding.series ?? (widget.query?.time?.compare ? 'comparison_period' : null)
  const key = (value: Scalar) => JSON.stringify(value)
  const categories = [...new Map(result.rows.map(row => [key(row[index(x)]), row[index(x)]])).values()]
  const groups: Scalar[] = seriesField
    ? [...new Map(result.rows.map(row => [key(row[index(seriesField)]), row[index(seriesField)]] )).values()]
    : [null]
  return { categories, series: ys.flatMap(field => groups.map(group => {
    const rows = result.rows.filter(row => !seriesField || key(row[index(seriesField)]) === key(group))
    const byCategory = new Map(rows.map(row => [key(row[index(x)]), row]))
    return {
      name: seriesField ? `${group ?? 'Unknown'} · ${field}` : field,
      values: categories.map(value => {
        const row = byCategory.get(key(value))
        const scalar = row?.[index(field)]
        return scalar == null ? null : Number(scalar)
      }),
      points: rows.map(row => [Number(row[index(x)]), row[index(field)] == null ? null : Number(row[index(field)])]),
    }
  })) }
}
