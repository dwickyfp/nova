import { redirect, createFileRoute } from '@tanstack/react-router'
import { SignIn2 } from '@/features/auth/sign-in/sign-in-2'

export const Route = createFileRoute('/(auth)/sign-in-2')({
  beforeLoad: () => {
    throw redirect({ to: '/sign-in', replace: true })
  },
  component: SignIn2,
})
