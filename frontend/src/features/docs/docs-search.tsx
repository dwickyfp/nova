import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { FileText, Search } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { cn } from '@/lib/utils'
import {
  docsLink,
  docsPages,
  docsPath,
  docsSections,
  searchDocs,
  type DocsPage,
} from './content'

const sectionTitle = new Map(docsSections.map((s) => [s.id, s.title]))

export function DocsSearch({ className }: { className?: string }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target
      const typing =
        target instanceof Element &&
        target.closest('input, textarea, [contenteditable="true"]') !== null
      if ((event.key === 'k' && (event.metaKey || event.ctrlKey)) || (event.key === '/' && !typing)) {
        event.preventDefault()
        setOpen((value) => !value)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const hits = useMemo(() => searchDocs(query), [query])
  const suggestions = useMemo(
    () => docsSections.map((section) => section.pages[0]).filter(Boolean),
    [],
  )

  function go(page: DocsPage, hash?: string) {
    setOpen(false)
    setQuery('')
    void navigate({ ...docsLink(page), hash })
  }

  return (
    <>
      <button
        type='button'
        onClick={() => setOpen(true)}
        className={cn(
          'flex h-9 items-center gap-2 rounded-md border border-input bg-background px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
          className,
        )}
      >
        <Search className='size-4 shrink-0' aria-hidden />
        <span className='sr-only sm:not-sr-only sm:flex-1 sm:truncate sm:text-start'>Search docs</span>
        <kbd className='hidden rounded border border-surface-border bg-surface-1 px-1.5 font-mono text-xs sm:inline'>
          /
        </kbd>
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className='gap-0 overflow-hidden p-0 sm:max-w-xl'>
          <DialogTitle className='sr-only'>Search docs</DialogTitle>
          <DialogDescription className='sr-only'>
            {`Search ${docsPages.length} documentation pages`}
          </DialogDescription>
          <Command shouldFilter={false} className='**:[[cmdk-group-heading]]:px-2 **:[[cmdk-group-heading]]:text-muted-foreground **:[[cmdk-item]]:py-2.5'>
        <CommandInput
          placeholder='Search, for example "create user" or "stage"'
          value={query}
          onValueChange={setQuery}
        />
        <CommandList className='max-h-[min(60vh,28rem)]'>
          {query.trim() === '' ? (
            <CommandGroup heading='Start with a section'>
              {suggestions.map((page) => (
                <CommandItem
                  key={docsPath(page)}
                  value={docsPath(page)}
                  onSelect={() => go(page)}
                >
                  <FileText className='size-4' aria-hidden />
                  <span className='truncate'>{sectionTitle.get(page.section)}</span>
                  <span className='ms-auto truncate text-xs text-muted-foreground'>{page.title}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          ) : (
            <>
              <CommandEmpty>
                No page mentions “{query.trim()}”. Try a shorter or different word.
              </CommandEmpty>
              {hits.length > 0 && (
                <CommandGroup heading={`${hits.length} result${hits.length === 1 ? '' : 's'}`}>
                  {hits.map(({ page, heading, snippet }) => (
                    <CommandItem
                      key={docsPath(page)}
                      value={docsPath(page)}
                      onSelect={() => go(page, heading?.id)}
                      className='flex-col items-start gap-0.5'
                    >
                      <span className='flex w-full items-center gap-2'>
                        <FileText className='size-4 shrink-0' aria-hidden />
                        <span className='truncate font-medium'>{page.title}</span>
                        <span className='ms-auto shrink-0 text-xs text-muted-foreground'>
                          {sectionTitle.get(page.section)}
                        </span>
                      </span>
                      {heading && (
                        <span className='ps-6 text-xs text-foreground'>{heading.text}</span>
                      )}
                      <span className='line-clamp-2 ps-6 text-xs text-muted-foreground'>{snippet}</span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
            </>
          )}
        </CommandList>
          </Command>
        </DialogContent>
      </Dialog>
    </>
  )
}
