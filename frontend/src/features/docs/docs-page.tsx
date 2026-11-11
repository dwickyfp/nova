import { useEffect } from 'react'
import { Link, useLocation } from '@tanstack/react-router'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { docsLink, docsSections, neighbours, type DocsPage } from './content'
import { DocsMarkdown } from './docs-markdown'

function OnThisPage({ page }: { page: DocsPage }) {
  if (page.headings.length < 2) return null
  return (
    <nav aria-label='On this page' className='sticky top-22 hidden max-h-[calc(100svh-7rem)] w-56 shrink-0 overflow-y-auto xl:block'>
      <h2 className='mb-2 text-xs font-medium text-muted-foreground'>On this page</h2>
      <ul className='space-y-1 border-s border-surface-border text-sm'>
        {page.headings.map((heading) => (
          <li key={heading.id}>
            <a
              href={`#${heading.id}`}
              className={cn(
                '-ms-px block border-s border-transparent py-0.5 text-muted-foreground transition-colors hover:border-foreground hover:text-foreground',
                heading.depth === 3 ? 'ps-6' : 'ps-3',
              )}
            >
              {heading.text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  )
}

export function DocsPageView({ page }: { page: DocsPage }) {
  const { hash } = useLocation()
  const { previous, next } = neighbours(page)
  const section = docsSections.find((s) => s.id === page.section)

  useEffect(() => {
    const previousTitle = document.title
    document.title = `${page.title} · Nova docs`
    if (hash) {
      document.getElementById(hash)?.scrollIntoView()
    } else {
      window.scrollTo({ top: 0 })
    }
    return () => { document.title = previousTitle }
  }, [page, hash])

  return (
    <div className='flex gap-10'>
      <article className='min-w-0 max-w-3xl flex-1'>
        <p className='mb-2 text-sm text-muted-foreground'>{section?.title}</p>
        <h1 className='text-2xl font-semibold tracking-tight sm:text-3xl'>{page.title}</h1>
        {page.description && (
          <p className='mt-3 text-base leading-7 text-muted-foreground'>{page.description}</p>
        )}
        <div className='mt-6'>
          <DocsMarkdown>{page.body}</DocsMarkdown>
        </div>
        <nav aria-label='Previous and next page' className='mt-14 grid gap-3 border-t border-surface-border pt-6 sm:grid-cols-2'>
          {previous ? (
            <Link
              {...docsLink(previous)}
              className='group rounded-lg border border-surface-border p-4 transition-colors hover:bg-surface-1'
            >
              <span className='flex items-center gap-1 text-xs text-muted-foreground'>
                <ChevronLeft className='size-3.5' aria-hidden /> Previous
              </span>
              <span className='mt-1 block font-medium'>{previous.title}</span>
            </Link>
          ) : (
            <span />
          )}
          {next && (
            <Link
              {...docsLink(next)}
              className='group rounded-lg border border-surface-border p-4 text-end transition-colors hover:bg-surface-1'
            >
              <span className='flex items-center justify-end gap-1 text-xs text-muted-foreground'>
                Next <ChevronRight className='size-3.5' aria-hidden />
              </span>
              <span className='mt-1 block font-medium'>{next.title}</span>
            </Link>
          )}
        </nav>
      </article>
      <OnThisPage page={page} />
    </div>
  )
}

export function DocsHome() {
  useEffect(() => {
    const previousTitle = document.title
    document.title = 'Nova docs'
    window.scrollTo({ top: 0 })
    return () => { document.title = previousTitle }
  }, [])

  return (
    <div className='max-w-5xl'>
      <h1 className='text-2xl font-semibold tracking-tight sm:text-3xl'>Nova documentation</h1>
      <p className='mt-3 max-w-2xl text-base leading-7 text-muted-foreground'>
        Step-by-step guides for everything you can do in Nova, written for people who are new to
        data tools. Each guide explains the permissions and setup you need before you start.
      </p>
      <div className='mt-10 grid gap-4 sm:grid-cols-2 xl:grid-cols-3'>
        {docsSections.map((section) => (
          <section key={section.id} className='flex flex-col rounded-lg border border-surface-border bg-surface-2 p-5'>
            <h2 className='font-semibold'>
              <Link {...docsLink(section.pages[0])} className='hover:underline'>
                {section.title}
              </Link>
            </h2>
            <p className='mt-1.5 text-sm leading-6 text-muted-foreground'>{section.summary}</p>
            <ul className='mt-4 space-y-1.5 text-sm'>
              {section.pages.slice(0, 5).map((page) => (
                <li key={page.slug}>
                  <Link {...docsLink(page)} className='text-info-strong hover:underline'>
                    {page.title}
                  </Link>
                </li>
              ))}
            </ul>
            {section.pages.length > 5 && (
              <p className='mt-3 text-xs text-muted-foreground'>
                and {section.pages.length - 5} more in the menu
              </p>
            )}
          </section>
        ))}
      </div>
    </div>
  )
}

export function DocsNotFound() {
  return (
    <div className='max-w-xl'>
      <h1 className='text-2xl font-semibold tracking-tight'>Page not found</h1>
      <p className='mt-3 leading-7 text-muted-foreground'>
        This documentation page does not exist or was renamed. Use the menu or search to find the
        topic, or go back to the docs home.
      </p>
      <Link to='/docs' className='mt-6 inline-block font-medium text-info-strong hover:underline'>
        Go to the docs home
      </Link>
    </div>
  )
}
