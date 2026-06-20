import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { SearchableSelect } from '@/components/ui/searchable-select'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

export const DEFAULT_PAGE_SIZES = [10, 25, 50]

type SimpleTableViewportProps = {
  children: React.ReactNode
  className?: string
}

export function SimpleTableViewport({
  children,
  className,
}: SimpleTableViewportProps) {
  return (
    <div
      className={cn(
        'relative max-h-[56vh] min-h-0 overflow-auto overscroll-contain rounded-lg border border-border bg-background',
        '[&>table>thead]:sticky [&>table>thead]:top-0 [&>table>thead]:z-20',
        '[&>table>thead]:border-b [&>table>thead]:border-border [&>table>thead]:bg-muted',
        '[&>table>tbody]:bg-background',
        className
      )}
    >
      {children}
    </div>
  )
}

type SimpleTableFilter = {
  label: string
  value: string
  options: string[]
  onChange: (value: string) => void
  icon?: React.ReactNode
}

type SimpleTableToolbarProps = {
  search?: string
  onSearchChange?: (value: string) => void
  searchPlaceholder?: string
  resultLabel: string
  filters?: SimpleTableFilter[]
  actions?: React.ReactNode
}

export function SimpleTableToolbar({
  search,
  onSearchChange,
  searchPlaceholder,
  resultLabel,
  filters = [],
  actions,
}: SimpleTableToolbarProps) {
  return (
    <div className='flex flex-wrap items-center gap-2'>
      {onSearchChange ? (
        <Input
          placeholder={searchPlaceholder ?? 'Search...'}
          value={search ?? ''}
          onChange={(event) => onSearchChange(event.target.value)}
          className='h-9 w-full sm:max-w-xs'
        />
      ) : null}
      {filters.map((filter) => (
        <SearchableSelect
          key={filter.label}
          options={filter.options}
          value={filter.value}
          onChange={filter.onChange}
          label={filter.label}
          icon={filter.icon}
          className='h-9'
        />
      ))}
      <div className='ml-auto flex items-center gap-2'>
        <span className='text-sm text-muted-foreground'>{resultLabel}</span>
        {actions}
      </div>
    </div>
  )
}

type SimpleTablePaginationProps = {
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  pageSizes?: number[]
}

export function SimpleTablePagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizes = DEFAULT_PAGE_SIZES,
}: SimpleTablePaginationProps) {
  if (total === 0) return null

  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const currentPage = Math.min(Math.max(page, 1), totalPages)
  const pageStart = (currentPage - 1) * pageSize + 1
  const pageEnd = Math.min(currentPage * pageSize, total)

  return (
    <div className='mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between'>
      <div className='flex items-center gap-2 text-sm text-muted-foreground'>
        <span>Rows per page</span>
        <Select
          value={String(pageSize)}
          onValueChange={(value) => onPageSizeChange(Number(value))}
        >
          <SelectTrigger className='h-8 w-[70px]'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {pageSizes.map((size) => (
              <SelectItem key={size} value={String(size)}>
                {size}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className='flex flex-wrap items-center gap-3 text-sm text-muted-foreground'>
        <span>
          Showing {pageStart}–{pageEnd} of {total}
        </span>
        <div className='flex items-center gap-1'>
          <Button
            variant='ghost'
            size='sm'
            className='h-7 w-7 p-0'
            disabled={currentPage === 1}
            onClick={() => onPageChange(1)}
          >
            <span className='sr-only'>Go to first page</span>
            <ChevronsLeft className='size-3.5' />
          </Button>
          <Button
            variant='ghost'
            size='sm'
            className='h-7 w-7 p-0'
            disabled={currentPage === 1}
            onClick={() => onPageChange(Math.max(1, currentPage - 1))}
          >
            <span className='sr-only'>Go to previous page</span>
            <ChevronLeft className='size-3.5' />
          </Button>
          <span className='min-w-14 px-1 text-center'>
            {currentPage} / {totalPages}
          </span>
          <Button
            variant='ghost'
            size='sm'
            className='h-7 w-7 p-0'
            disabled={currentPage === totalPages}
            onClick={() => onPageChange(Math.min(totalPages, currentPage + 1))}
          >
            <span className='sr-only'>Go to next page</span>
            <ChevronRight className='size-3.5' />
          </Button>
          <Button
            variant='ghost'
            size='sm'
            className='h-7 w-7 p-0'
            disabled={currentPage === totalPages}
            onClick={() => onPageChange(totalPages)}
          >
            <span className='sr-only'>Go to last page</span>
            <ChevronsRight className='size-3.5' />
          </Button>
        </div>
      </div>
    </div>
  )
}
