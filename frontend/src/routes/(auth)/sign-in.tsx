import { z } from 'zod'
import { createFileRoute, redirect } from '@tanstack/react-router'
import { useAuthStore } from '@/stores/auth-store'
import { SignIn2 } from '@/features/auth/sign-in/sign-in-2'

const searchSchema = z.object({
  redirect: z.string().optional(),
})

export const Route = createFileRoute('/(auth)/sign-in')({
  beforeLoad: () => {
    const { accessToken } = useAuthStore.getState().auth
    if (accessToken) {
      throw redirect({ to: '/', replace: true })
    }
  },
  validateSearch: searchSchema,
  component: SignIn2,
})
