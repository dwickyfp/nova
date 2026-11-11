import { useState, type ReactNode } from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import { ArrowLeft, Menu } from 'lucide-react'
import { Logo } from '@/assets/logo'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { docsLink, docsPath, docsSections } from './content'
import { DocsSearch } from './docs-search'

function DocsNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  return (
    <nav aria-label='Documentation' className='space-y-6 pb-10'>
      <Link
        to='/docs'
        onClick={onNavigate}
        className={cn(
          'block rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-accent',
          pathname === '/docs' || pathname === '/docs/'
            ? 'bg-accent font-medium text-foreground'
            : 'text-muted-foreground hover:text-foreground',
        )}
      >
        Docs home
      </Link>
      {docsSections.map((section) => (
        <div key={section.id}>
          <h2 className='mb-1.5 px-2 text-xs font-medium text-muted-foreground'>
            {section.title}
          </h2>
          <ul className='space-y-0.5'>
            {section.pages.map((page) => {
              const path = docsPath(page)
              const active = pathname === path
              return (
                <li key={path}>
                  <Link
                    {...docsLink(page)}
                    onClick={onNavigate}
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'block rounded-md px-2 py-1.5 text-sm leading-snug transition-colors hover:bg-accent',
                      active
                        ? 'bg-accent font-medium text-foreground'
                        : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {page.title}
                  </Link>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </nav>
  )
}

export function DocsLayout({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false)
  return (
    <div className='min-h-svh bg-background text-foreground'>
      <header className='sticky top-0 z-30 border-b border-surface-border bg-background'>
        <div className='mx-auto flex h-14 max-w-screen-2xl items-center gap-3 px-4 sm:px-6'>
          <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
            <SheetTrigger asChild>
              <Button variant='ghost' size='icon' className='size-11 sm:size-9 lg:hidden' aria-label='Open documentation menu'>
                <Menu className='size-5' />
              </Button>
            </SheetTrigger>
            <SheetContent side='left' className='w-80 overflow-y-auto p-0'>
              <SheetHeader className='border-b border-surface-border'>
                <SheetTitle>Nova docs</SheetTitle>
                <SheetDescription className='sr-only'>All documentation pages</SheetDescription>
              </SheetHeader>
              <div className='px-3 pt-4'>
                <Link
                  to='/'
                  className='mb-4 flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground sm:hidden'
                >
                  <ArrowLeft className='size-4' />
                  Back to Nova
                </Link>
                <DocsNav onNavigate={() => setMenuOpen(false)} />
              </div>
            </SheetContent>
          </Sheet>
          <Link to='/docs' className='flex shrink-0 items-center gap-2.5 rounded-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'>
            <Logo className='size-7' />
            <span className='text-lg leading-none font-medium text-primary'>nova</span>
            <span className='text-sm text-muted-foreground'>Docs</span>
          </Link>
          <div className='ms-auto flex items-center gap-2'>
            <DocsSearch className='size-11 justify-center sm:h-9 sm:w-64' />
            <Button variant='ghost' size='sm' asChild className='hidden sm:inline-flex'>
              <Link to='/'>
                <ArrowLeft className='size-4' />
                Back to Nova
              </Link>
            </Button>
          </div>
        </div>
      </header>
      <div className='mx-auto flex max-w-screen-2xl gap-8 px-4 sm:px-6'>
        <aside className='sticky top-14 hidden max-h-[calc(100svh-3.5rem)] w-64 shrink-0 overflow-y-auto pt-8 lg:block'>
          <DocsNav />
        </aside>
        <main className='min-w-0 flex-1 pt-8 pb-16'>{children}</main>
      </div>
    </div>
  )
}
