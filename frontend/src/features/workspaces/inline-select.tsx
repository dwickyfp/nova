import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Check, Search } from 'lucide-react'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { cn } from '@/lib/utils'

export type InlineSelectProps = {
  label: string
  value: string
  options: string[]
  icon: ReactNode
  onChange: (value: string) => void
}

export function InlineSelect({
  label,
  value,
  options,
  icon,
  onChange,
}: InlineSelectProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    if (!search) return options
    const q = search.toLowerCase()
    return options.filter((o) => o.toLowerCase().includes(q))
  }, [options, search])

  return (
    <Popover
      open={open}
      onOpenChange={(v) => {
        setOpen(v)
        if (!v) setSearch('')
      }}
    >
      <PopoverTrigger asChild>
        <button
          type='button'
          className='flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors hover:bg-muted/60'
        >
          <span className='text-muted-foreground'>{icon}</span>
          <span>{value || label}</span>
        </button>
      </PopoverTrigger>

      <PopoverContent align='end' className='w-56 p-0' sideOffset={8}>
        <div className='p-2'>
          <div className='relative'>
            <Search className='absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground' />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={label}
              className='h-7 w-full rounded-md border border-border bg-background pl-8 pr-2 text-xs outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-primary'
            />
          </div>
        </div>
        <div className='max-h-[240px] overflow-auto py-1'>
          {filtered.length === 0 && (
            <div className='px-3 py-4 text-center text-xs text-muted-foreground'>
              No results
            </div>
          )}
          {filtered.map((option) => {
            const isActive = option === value
            return (
              <button
                key={option}
                type='button'
                onClick={() => {
                  onChange(option)
                  setOpen(false)
                }}
                className={cn(
                  'flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors hover:bg-muted/50',
                  isActive ? 'text-primary' : 'text-foreground'
                )}
              >
                <span className='truncate'>{option}</span>
                {isActive && (
                  <Check className='ml-auto size-3.5 shrink-0 text-primary' />
                )}
              </button>
            )
          })}
        </div>
      </PopoverContent>
    </Popover>
  )
}
