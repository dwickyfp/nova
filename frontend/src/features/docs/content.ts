import { DOCS_SECTIONS, type DocsSectionMeta } from './nav'

export type DocsHeading = { depth: 2 | 3; text: string; id: string }

export type DocsPage = {
  section: string
  slug: string
  title: string
  description: string
  body: string
  headings: DocsHeading[]
  /** Plain text of the body, used by search. */
  text: string
}

export type DocsSection = DocsSectionMeta & { pages: DocsPage[] }

const PAGE_PATH = /\/content\/([a-z0-9-]+)\/(\d{2})-([a-z0-9-]+)\.md$/

/** Lowercase, hyphenated anchor id for a heading; matches the renderer. */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[`*_~[\]()]/g, '')
    .replace(/[^a-z0-9\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
}

export function parseFrontMatter(raw: string): {
  data: Record<string, string>
  body: string
} {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(raw)
  if (!match) return { data: {}, body: raw }
  const data: Record<string, string> = {}
  for (const line of match[1].split(/\r?\n/)) {
    const pair = /^([A-Za-z][\w-]*):\s*(.*)$/.exec(line)
    if (pair) data[pair[1]] = pair[2].trim().replace(/^(["'])(.*)\1$/, '$2')
  }
  return { data, body: raw.slice(match[0].length) }
}

/** Visible text of inline Markdown: drops links' targets and code marks. */
export function inlineText(markdown: string): string {
  return markdown
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[`*_~]/g, '')
    .trim()
}

export function extractHeadings(body: string): DocsHeading[] {
  const headings: DocsHeading[] = []
  let fenced = false
  for (const line of body.split(/\r?\n/)) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced
    if (fenced) continue
    const match = /^(##|###)\s+(.+?)\s*#*\s*$/.exec(line)
    if (!match) continue
    const text = inlineText(match[2])
    headings.push({
      depth: match[1].length as 2 | 3,
      text,
      id: slugify(text),
    })
  }
  return headings
}

function plainText(body: string): string {
  const prose = body
    .replace(/^\s*(```|~~~).*$/gm, '')
    .replace(/^\s*\|?[\s:|-]+\|[\s:|-]*$/gm, '')
    .replace(/\|/g, ' ')
    .replace(/^#+\s+/gm, '')
    .replace(/^>\s?/gm, '')
  return inlineText(prose)
    .replace(/\s+/g, ' ')
    .trim()
}

export function buildSections(
  files: Record<string, string>,
  sections: readonly DocsSectionMeta[] = DOCS_SECTIONS,
): DocsSection[] {
  const bySection = new Map<string, { order: string; page: DocsPage }[]>()
  for (const [path, raw] of Object.entries(files)) {
    const match = PAGE_PATH.exec(path)
    if (!match) continue
    const [, section, order, slug] = match
    const { data, body } = parseFrontMatter(raw)
    const page: DocsPage = {
      section,
      slug,
      title: data.title || slug.replace(/-/g, ' '),
      description: data.description ?? '',
      body,
      headings: extractHeadings(body),
      text: plainText(body),
    }
    const list = bySection.get(section) ?? []
    list.push({ order, page })
    bySection.set(section, list)
  }
  return sections
    .map((meta) => ({
      ...meta,
      pages: (bySection.get(meta.id) ?? [])
        .sort((a, b) => a.order.localeCompare(b.order))
        .map((entry) => entry.page),
    }))
    .filter((section) => section.pages.length > 0)
}

const files = import.meta.glob<string>('./content/*/*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
})

export const docsSections: DocsSection[] = buildSections(files)

export const docsPages: DocsPage[] = docsSections.flatMap(
  (section) => section.pages,
)

export function docsPath(page: Pick<DocsPage, 'section' | 'slug'>): string {
  return `/docs/${page.section}/${page.slug}`
}

/** Typed router link to a page: `<Link {...docsLink(page)} />`. */
export function docsLink(page: Pick<DocsPage, 'section' | 'slug'>) {
  return {
    to: '/docs/$section/$slug' as const,
    params: { section: page.section, slug: page.slug },
  }
}

/** Parses an in-docs href such as `/docs/sql/stages#limits`. */
export function parseDocsHref(
  href: string,
): { section?: string; slug?: string; hash?: string } | null {
  const match = /^\/docs(?:\/([a-z0-9-]+)\/([a-z0-9-]+))?\/?(?:#(.*))?$/.exec(href)
  if (!match) return null
  return { section: match[1], slug: match[2], hash: match[3] }
}

export function findPage(section: string, slug: string): DocsPage | undefined {
  return docsPages.find((page) => page.section === section && page.slug === slug)
}

export function neighbours(page: DocsPage): {
  previous?: DocsPage
  next?: DocsPage
} {
  const index = docsPages.indexOf(page)
  return {
    previous: index > 0 ? docsPages[index - 1] : undefined,
    next: index >= 0 ? docsPages[index + 1] : undefined,
  }
}

export type DocsSearchHit = {
  page: DocsPage
  heading?: DocsHeading
  snippet: string
}

/**
 * Every word of the query must appear in the page; title and heading matches
 * rank above body matches. Returns at most `limit` hits.
 */
export function searchDocs(
  query: string,
  pages: DocsPage[] = docsPages,
  limit = 20,
): DocsSearchHit[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (terms.length === 0) return []
  const scored: { hit: DocsSearchHit; score: number }[] = []
  for (const page of pages) {
    const title = page.title.toLowerCase()
    const description = page.description.toLowerCase()
    const text = page.text.toLowerCase()
    const haystack = `${title} ${description} ${text}`
    if (!terms.every((term) => haystack.includes(term))) continue
    let score = 0
    for (const term of terms) {
      if (title.includes(term)) score += 10
      if (description.includes(term)) score += 4
    }
    const heading = page.headings.find((h) =>
      terms.every((term) => h.text.toLowerCase().includes(term)),
    )
    if (heading) score += 6
    scored.push({
      hit: { page, heading, snippet: snippetFor(page, terms[0]) },
      score,
    })
  }
  return scored
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map((entry) => entry.hit)
}

function snippetFor(page: DocsPage, term: string): string {
  const index = page.text.toLowerCase().indexOf(term)
  if (index < 0) return page.description
  const start = Math.max(0, index - 60)
  const end = Math.min(page.text.length, index + 100)
  return `${start > 0 ? '…' : ''}${page.text.slice(start, end)}${end < page.text.length ? '…' : ''}`
}
