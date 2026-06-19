import { useMemo, useState } from 'react'
import { Check, ChevronDown, Search } from 'lucide-react'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { cn } from '@/lib/utils'

export type SearchableSelectProps = {
  options: string[]
  value: string
  onChange: (value: string) => void
  label: string
  placeholder?: string
  icon?: React.ReactNode
  className?: string
}

export function SearchableSelect({
  options,
  value,
  onChange,
  label,
  placeholder,
  icon,
  className,
}: SearchableSelectProps) {
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
          className={cn(
            'flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-sm transition-colors hover:bg-muted/60',
            className,
          )}
        >
          {icon && (
            <span className='text-muted-foreground'>{icon}</span>
          )}
          <span className='truncate'>
            {value || `All ${label}`}
          </span>
          <ChevronDown className='ml-auto size-3.5 shrink-0 text-muted-foreground' />
        </button>
      </PopoverTrigger>

      <PopoverContent align='end' className='w-56 p-0' sideOffset={8}>
        <div className='p-2'>
          <div className='relative'>
            <Search className='absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground' />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={placeholder ?? `Search ${label.toLowerCase()}…`}
              className='h-7 w-full rounded-md border border-border bg-background pl-8 pr-2 text-xs outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-primary'
            />
          </div>
        </div>
        <div className='max-h-[240px] overflow-auto py-1'>
          {/* "All" option */}
          <button
            key='__all__'
            type='button'
            onClick={() => {
              onChange('')
              setOpen(false)
            }}
            className={cn(
              'flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors hover:bg-muted/50',
              value === '' ? 'text-primary' : 'text-foreground',
            )}
          >
            <span className='truncate'>All {label}</span>
            {value === '' && (
              <Check className='ml-auto size-3.5 shrink-0 text-primary' />
            )}
          </button>

          {/* Filtered options */}
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
                  isActive ? 'text-primary' : 'text-foreground',
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
