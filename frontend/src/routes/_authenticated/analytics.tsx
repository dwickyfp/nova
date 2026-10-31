import { useCallback } from 'react'
import { z } from 'zod'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { BiWorkspace } from '@/features/bi/bi-workspace'

/** Superset framed inside Nova; `path` is the Superset view being shown. */
const searchSchema = z.object({
  path: z.string().max(2048).optional(),
})

export const Route = createFileRoute('/_authenticated/analytics')({
  validateSearch: searchSchema,
  component: RouteComponent,
})

function RouteComponent() {
  const { path } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const followPath = useCallback(
    (next: string) => navigate({ search: { path: next }, replace: true }),
    [navigate],
  )
  return <BiWorkspace path={path} onPathChange={followPath} />
}
