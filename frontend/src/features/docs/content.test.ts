import { describe, expect, it } from 'vitest'
import {
  buildSections,
  docsPages,
  extractHeadings,
  findPage,
  parseDocsHref,
  parseFrontMatter,
  searchDocs,
  slugify,
} from './content'
import { calloutKind } from './docs-markdown'
import { DOCS_SECTIONS } from './nav'

const sample = `---
title: Load files
description: Bring a CSV into a table.
---

## Before you start

\`\`\`sql
-- ## not a heading
SELECT 1;
\`\`\`

### Use the \`COPY\` command
`

describe('docs content parsing', () => {
  it('reads front matter and strips it from the body', () => {
    const { data, body } = parseFrontMatter(sample)
    expect(data).toEqual({ title: 'Load files', description: 'Bring a CSV into a table.' })
    expect(body.startsWith('\n## Before you start')).toBe(true)
  })

  it('collects headings outside code fences with renderer-matching ids', () => {
    expect(extractHeadings(parseFrontMatter(sample).body)).toEqual([
      { depth: 2, text: 'Before you start', id: 'before-you-start' },
      { depth: 3, text: 'Use the COPY command', id: 'use-the-copy-command' },
    ])
    expect(slugify('What is a `stage`?')).toBe('what-is-a-stage')
  })

  it('orders pages by file prefix and drops unknown sections and files', () => {
    const sections = buildSections(
      {
        './content/sql/02-second.md': '---\ntitle: Second\n---\nBody',
        './content/sql/01-first.md': '---\ntitle: First\n---\nBody',
        './content/unknown/01-x.md': '---\ntitle: X\n---\n',
        './content/AUTHORING.md': '# Guide',
      },
      [{ id: 'sql', title: 'SQL', summary: '' }],
    )
    expect(sections).toHaveLength(1)
    expect(sections[0].pages.map((p) => p.slug)).toEqual(['first', 'second'])
  })

  it('parses in-docs links', () => {
    expect(parseDocsHref('/docs/sql/stages#limits')).toEqual({
      section: 'sql',
      slug: 'stages',
      hash: 'limits',
    })
    expect(parseDocsHref('/docs')).toEqual({ section: undefined, slug: undefined, hash: undefined })
    expect(parseDocsHref('https://example.com')).toBeNull()
  })

  it('recognises callouts', () => {
    expect(calloutKind('Note: read this')).toBe('note')
    expect(calloutKind('Warning: careful')).toBe('warning')
    expect(calloutKind('A quoted line')).toBeNull()
  })

  it('ranks title matches first and requires every word', () => {
    const pages = buildSections(
      {
        './content/sql/01-a.md': '---\ntitle: Create a user\n---\nUsers sign in.',
        './content/sql/02-b.md': '---\ntitle: Roles\n---\nYou can create a role for a user.',
        './content/sql/03-c.md': '---\ntitle: Stages\n---\nFiles only.',
      },
      [{ id: 'sql', title: 'SQL', summary: '' }],
    ).flatMap((s) => s.pages)
    expect(searchDocs('create user', pages).map((h) => h.page.slug)).toEqual(['a', 'b'])
    expect(searchDocs('   ', pages)).toEqual([])
  })
})

/**
 * Rules from content/AUTHORING.md that can be checked mechanically, run
 * against every published page.
 */
describe('published docs pages', () => {
  const sectionIds = new Set(DOCS_SECTIONS.map((s) => s.id))

  it('publishes every authored page with a unique route', () => {
    const paths = Object.keys(import.meta.glob('./content/*/*.md'))
    expect(docsPages).toHaveLength(paths.length)
    expect(new Set(docsPages.map((page) => `${page.section}/${page.slug}`)).size).toBe(paths.length)
    for (const page of docsPages) {
      expect(new Set(page.headings.map((heading) => heading.id)).size, `${page.section}/${page.slug}`).toBe(page.headings.length)
    }
    expect(docsPages.some((page) => page.slug === 'render-check')).toBe(false)
  })

  it('has a guide for every section in the navigation', () => {
    expect(new Set(docsPages.map((page) => page.section))).toEqual(sectionIds)
  })

  it('every page has a title and a description, and lives in a known section', () => {
    for (const page of docsPages) {
      expect(page.title, page.slug).not.toBe(page.slug.replace(/-/g, ' '))
      expect(page.description.length, `${page.section}/${page.slug}`).toBeGreaterThan(10)
      expect(sectionIds.has(page.section)).toBe(true)
    }
  })

  it('pages start at level-2 headings (the title is rendered for them)', () => {
    for (const page of docsPages) {
      const fenceless = page.body.replace(/```[\s\S]*?```/g, '')
      expect(/^#\s/m.test(fenceless), `${page.section}/${page.slug}`).toBe(false)
    }
  })

  it('every internal link points to an existing page and heading', () => {
    const broken: string[] = []
    for (const page of docsPages) {
      for (const [, href] of page.body.matchAll(/\]\((\/docs[^)\s]*)\)/g)) {
        const target = parseDocsHref(href)
        if (!target) {
          broken.push(`${page.section}/${page.slug} → ${href}`)
          continue
        }
        if (!target.section || !target.slug) continue
        const linked = findPage(target.section, target.slug)
        if (!linked || (target.hash && !linked.headings.some((h) => h.id === target.hash))) {
          broken.push(`${page.section}/${page.slug} → ${href}`)
        }
      }
    }
    expect(broken).toEqual([])
  })

  it('code blocks name their language', () => {
    const unlabeled: string[] = []
    for (const page of docsPages) {
      let open = false
      for (const line of page.body.split('\n')) {
        const fence = /^\s*```(\S*)/.exec(line)
        if (!fence) continue
        if (!open && !fence[1]) unlabeled.push(`${page.section}/${page.slug}`)
        open = !open
      }
    }
    expect(unlabeled).toEqual([])
  })

  it('does not expose storage vendors, physical paths, or internal API routes', () => {
    const banned = /s3:\/\/|gs:\/\/|\bminio\b|\bazure\b|\bgcs\b|\/api\/v1\//i
    const leaks = docsPages
      .filter((page) => banned.test(page.body))
      .map((page) => `${page.section}/${page.slug}: ${banned.exec(page.body)?.[0]}`)
    expect(leaks).toEqual([])
  })
})
