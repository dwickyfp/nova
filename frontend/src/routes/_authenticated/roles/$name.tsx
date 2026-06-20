import { createFileRoute } from '@tanstack/react-router'
import { RoleDetailPage } from '@/features/roles/role-detail'

function RoleDetailRoute() {
  const { name } = Route.useParams()
  return <RoleDetailPage name={name} />
}

export const Route = createFileRoute('/_authenticated/roles/$name')({
  component: RoleDetailRoute,
})
