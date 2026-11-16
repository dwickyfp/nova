/** Scheduled Excel exports of a dashboard page, as the dialog and tests read them. */
export type Cadence = 'daily' | 'weekly' | 'monthly'
export type ExportSchedule = {
  schedule_id: string
  page_id: string
  role: string
  timezone: string
  format: 'xlsx'
  mine: boolean
  cadence: Cadence
  time: string
  weekday?: number
  day?: number
}

export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

export function ordinal(day: number): string {
  const tail = day % 10 === 1 && day !== 11 ? 'st' : day % 10 === 2 && day !== 12 ? 'nd' : day % 10 === 3 && day !== 13 ? 'rd' : 'th'
  return `${day}${tail}`
}

/** "Every Monday at 07:00 (Asia/Jakarta)" */
export function describeSchedule(item: Pick<ExportSchedule, 'cadence' | 'time' | 'weekday' | 'day' | 'timezone'>): string {
  const when = item.cadence === 'weekly' ? `Every ${WEEKDAYS[item.weekday ?? 0]}`
    : item.cadence === 'monthly' ? `On the ${ordinal(item.day ?? 1)} of every month` : 'Every day'
  return `${when} at ${item.time} (${item.timezone.replace(/_/g, ' ')})`
}
