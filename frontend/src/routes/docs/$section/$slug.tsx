import { createFileRoute, notFound } from '@tanstack/react-router'
import { DocsNotFound, DocsPageView, findPage } from '@/features/docs'

export const Route = createFileRoute('/docs/$section/$slug')({
  loader: ({ params }) => {
    const page = findPage(params.section, params.slug)
    if (!page) throw notFound()
    return page
  },
  component: function DocsRoute() {
    const page = Route.useLoaderData()
    return <DocsPageView page={page} />
  },
  notFoundComponent: DocsNotFound,
})
