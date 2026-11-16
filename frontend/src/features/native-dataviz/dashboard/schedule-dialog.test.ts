import { describe, expect, it } from 'vitest'
import { describeSchedule } from './schedule'

describe('schedule descriptions', () => {
  it('reads each cadence in plain words with its time zone', () => {
    expect(describeSchedule({ cadence: 'daily', time: '07:30', timezone: 'UTC' })).toBe('Every day at 07:30 (UTC)')
    expect(describeSchedule({ cadence: 'weekly', time: '08:00', weekday: 0, timezone: 'Asia/Jakarta' }))
      .toBe('Every Monday at 08:00 (Asia/Jakarta)')
    expect(describeSchedule({ cadence: 'monthly', time: '06:00', day: 2, timezone: 'America/New_York' }))
      .toBe('On the 2nd of every month at 06:00 (America/New York)')
    expect(describeSchedule({ cadence: 'monthly', time: '06:00', day: 11, timezone: 'UTC' }))
      .toBe('On the 11th of every month at 06:00 (UTC)')
    expect(describeSchedule({ cadence: 'monthly', time: '06:00', day: 23, timezone: 'UTC' }))
      .toBe('On the 23rd of every month at 06:00 (UTC)')
  })
})
