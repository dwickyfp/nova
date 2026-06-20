import { createFileRoute } from '@tanstack/react-router'
import { UserDetailPage } from '@/features/users/user-detail'

function UserDetailRoute() {
  const { username } = Route.useParams()
  return <UserDetailPage username={username} />
}

export const Route = createFileRoute('/_authenticated/users/$username')({
  component: UserDetailRoute,
})
