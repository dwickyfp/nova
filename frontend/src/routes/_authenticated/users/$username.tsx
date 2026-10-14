import { createFileRoute } from '@tanstack/react-router'
import { UserDetailPage } from '@/features/users/user-detail'

export const Route = createFileRoute('/_authenticated/users/$username')({
  component: function UserDetailRoute() {
    const { username } = Route.useParams()
    return <UserDetailPage username={username} />
  },
})
