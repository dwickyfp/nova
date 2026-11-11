/**
 * Sections of the user documentation, in reading order. Each `id` is a folder
 * under `./content/`; the pages inside it are discovered automatically and
 * ordered by their two-digit file prefix (see `content/AUTHORING.md`).
 */
export type DocsSectionMeta = {
  id: string
  title: string
  /** One line shown on the docs home page. */
  summary: string
}

export const DOCS_SECTIONS: readonly DocsSectionMeta[] = [
  {
    id: 'getting-started',
    title: 'Getting started',
    summary: 'What Nova is, how to sign in, and how to find your way around.',
  },
  {
    id: 'sql',
    title: 'SQL',
    summary: 'Write and run SQL in a worksheet, from simple queries to Nova commands.',
  },
  {
    id: 'notebooks',
    title: 'Notebooks',
    summary: 'Mix SQL and Python in one document, and reach every catalog with novaduck.',
  },
  {
    id: 'tasks',
    title: 'Tasks and streams',
    summary: 'Run work on a schedule, and react when a table changes.',
  },
  {
    id: 'data',
    title: 'Data management',
    summary: 'Browse databases, manage stages and connections, and bring data in.',
  },
  {
    id: 'ai-ml',
    title: 'AI & ML',
    summary: 'Agents, semantic views, the business ontology, search, and machine learning.',
  },
  {
    id: 'studio',
    title: 'Nova Studio',
    summary: 'Ask questions about your data in plain language and get governed answers.',
  },
  {
    id: 'dataviz',
    title: 'Dataviz',
    summary: 'Build charts and dashboards that follow your role.',
  },
  {
    id: 'access-control',
    title: 'Access control',
    summary: 'Users, roles, and who may see which data.',
  },
  {
    id: 'administration',
    title: 'Administration',
    summary: 'AI providers, system settings, and your own preferences.',
  },
  {
    id: 'monitoring',
    title: 'Monitoring',
    summary: 'Follow queries, data loads, AI usage, and system health.',
  },
]
