import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { api, ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import type { Page } from './layout'

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

function ordinal(day: number): string {
  const tail = day % 10 === 1 && day !== 11 ? 'st' : day % 10 === 2 && day !== 12 ? 'nd' : day % 10 === 3 && day !== 13 ? 'rd' : 'th'
  return `${day}${tail}`
}

/** "Every Monday at 07:00 (Asia/Jakarta)" */
export function describeSchedule(item: Pick<ExportSchedule, 'cadence' | 'time' | 'weekday' | 'day' | 'timezone'>): string {
  const when = item.cadence === 'weekly' ? `Every ${WEEKDAYS[item.weekday ?? 0]}`
    : item.cadence === 'monthly' ? `On the ${ordinal(item.day ?? 1)} of every month` : 'Every day'
  return `${when} at ${item.time} (${item.timezone.replace(/_/g, ' ')})`
}

function zones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.('timeZone') ?? ['UTC']
  } catch {
    return ['UTC']
  }
}

/**
 * Save a page as an Excel workbook on a timetable. Files appear in the
 * dashboard's shared workspace and hold what the active role can read.
 */
export function ScheduleDialog({ open, onOpenChange, dashboardId, pages, pageId }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  dashboardId: string
  pages: Page[]
  pageId: string
}) {
  const epoch = useAuthStore(state => state.securityEpoch)
  const client = useQueryClient()
  const path = `/viz/dashboards/${encodeURIComponent(dashboardId)}/schedules`
  const key = ['native-dataviz', 'export-schedules', epoch, dashboardId]
  const listing = useQuery({
    queryKey: key, enabled: open, retry: false,
    queryFn: ({ signal }) => api.request<ExportSchedule[]>(path, { signal, securityEpoch: epoch }),
  })
  const local = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', [])
  const [page, setPage] = useState(pageId)
  const [cadence, setCadence] = useState<Cadence>('weekly')
  const [weekday, setWeekday] = useState(0)
  const [day, setDay] = useState(1)
  const [time, setTime] = useState('07:00')
  const [timezone, setTimezone] = useState(local)
  const [problem, setProblem] = useState<string | null>(null)
  const zoneList = useMemo(() => zones(), [])
  const pageTitle = (id: string) => pages.find(item => item.page_id === id)?.title ?? 'A removed page'

  const save = useMutation({
    mutationFn: () => api.request<ExportSchedule>(path, {
      method: 'PUT', securityEpoch: epoch,
      body: JSON.stringify({
        page_id: page, cadence, time, timezone,
        ...(cadence === 'weekly' ? { weekday } : {}), ...(cadence === 'monthly' ? { day } : {}),
      }),
    }),
    onMutate: () => setProblem(null),
    onSuccess: saved => {
      void client.invalidateQueries({ queryKey: key })
      toast.success(`Scheduled: ${describeSchedule(saved)}`)
    },
    onError: error => setProblem(error instanceof ApiError && error.status !== 500
      ? error.message : 'The schedule could not be saved. Try again.'),
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.request(`${path}/${encodeURIComponent(id)}`, { method: 'DELETE', body: '{}', securityEpoch: epoch }),
    onSuccess: () => { void client.invalidateQueries({ queryKey: key }) },
    onError: () => toast.error('The schedule could not be removed.'),
  })

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className='sm:max-w-lg'>
      <DialogHeader>
        <DialogTitle>Schedule an Excel export</DialogTitle>
        <DialogDescription>
          A workbook of the page, one sheet per chart, is saved in this dashboard's workspace under
          Scheduled exports. It runs as your current role and holds only what that role can read.
        </DialogDescription>
      </DialogHeader>

      <section aria-label='Current schedules' className='space-y-2'>
        {listing.isPending ? <p className='text-sm text-muted-foreground'>Loading schedules…</p>
          : listing.isError ? <p className='text-sm text-muted-foreground'>Schedules could not be loaded.</p>
            : !listing.data.length ? <p className='text-sm text-muted-foreground'>Nothing is scheduled yet.</p>
              : <ul className='divide-y rounded-md border'>
                {listing.data.map(item => <li key={item.schedule_id} className='flex items-center gap-3 px-3 py-2'>
                  <CalendarClock className='size-4 shrink-0 text-muted-foreground' aria-hidden />
                  <div className='min-w-0 flex-1'>
                    <p className='truncate text-sm'>{pageTitle(item.page_id)}</p>
                    <p className='truncate text-xs text-muted-foreground'>{describeSchedule(item)} · as {item.role}</p>
                  </div>
                  {item.mine && <Button variant='ghost' size='icon' className='size-8 shrink-0' aria-label={`Remove the schedule for ${pageTitle(item.page_id)}`}
                    disabled={remove.isPending} onClick={() => remove.mutate(item.schedule_id)}><Trash2 /></Button>}
                </li>)}
              </ul>}
      </section>

      <div className='grid gap-3 sm:grid-cols-2'>
        <div className='space-y-1.5 sm:col-span-2'>
          <Label htmlFor='schedule-page' className='text-xs text-muted-foreground'>Page</Label>
          <Select value={page} onValueChange={setPage}>
            <SelectTrigger id='schedule-page' className='w-full'><SelectValue /></SelectTrigger>
            <SelectContent>{pages.map(item => <SelectItem key={item.page_id} value={item.page_id}>{item.title}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className='space-y-1.5'>
          <Label htmlFor='schedule-cadence' className='text-xs text-muted-foreground'>Repeat</Label>
          <Select value={cadence} onValueChange={value => setCadence(value as Cadence)}>
            <SelectTrigger id='schedule-cadence' className='w-full'><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value='daily'>Every day</SelectItem>
              <SelectItem value='weekly'>Every week</SelectItem>
              <SelectItem value='monthly'>Every month</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {cadence === 'weekly' && <div className='space-y-1.5'>
          <Label htmlFor='schedule-weekday' className='text-xs text-muted-foreground'>On</Label>
          <Select value={String(weekday)} onValueChange={value => setWeekday(Number(value))}>
            <SelectTrigger id='schedule-weekday' className='w-full'><SelectValue /></SelectTrigger>
            <SelectContent>{WEEKDAYS.map((name, index) => <SelectItem key={name} value={String(index)}>{name}</SelectItem>)}</SelectContent>
          </Select>
        </div>}
        {cadence === 'monthly' && <div className='space-y-1.5'>
          <Label htmlFor='schedule-day' className='text-xs text-muted-foreground'>Day of the month</Label>
          <Select value={String(day)} onValueChange={value => setDay(Number(value))}>
            <SelectTrigger id='schedule-day' className='w-full'><SelectValue /></SelectTrigger>
            <SelectContent>{Array.from({ length: 28 }, (_, index) => index + 1).map(value =>
              <SelectItem key={value} value={String(value)}>{ordinal(value)}</SelectItem>)}</SelectContent>
          </Select>
        </div>}
        <div className='space-y-1.5'>
          <Label htmlFor='schedule-time' className='text-xs text-muted-foreground'>Time</Label>
          <Input id='schedule-time' type='time' step={60} value={time} onChange={event => setTime(event.target.value.slice(0, 5))} />
        </div>
        <div className='space-y-1.5'>
          <Label htmlFor='schedule-zone' className='text-xs text-muted-foreground'>Time zone</Label>
          <Select value={timezone} onValueChange={setTimezone}>
            <SelectTrigger id='schedule-zone' className='w-full'><SelectValue /></SelectTrigger>
            <SelectContent className='max-h-72'>{zoneList.map(zone => <SelectItem key={zone} value={zone}>{zone.replace(/_/g, ' ')}</SelectItem>)}</SelectContent>
          </Select>
        </div>
      </div>
      {problem && <p role='alert' className='rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive'>{problem}</p>}
      <DialogFooter>
        <Button variant='outline' onClick={() => onOpenChange(false)}>Close</Button>
        <Button disabled={save.isPending || !/^\d{2}:\d{2}$/.test(time)} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save schedule'}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
