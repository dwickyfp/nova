import { useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate } from '@tanstack/react-router'
import { Loader2, LogIn, KeyRound } from 'lucide-react'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { PasswordInput } from '@/components/password-input'

// ── Login form schema ────────────────────────────────────────
const loginSchema = z.object({
  username: z.string().min(1, 'Please enter your username.'),
  password: z.string().min(1, 'Please enter your password.'),
})

// ── Setup form schema ────────────────────────────────────────
const setupSchema = z
  .object({
    newPassword: z
      .string()
      .min(1, 'Please enter a new password.')
      .min(6, 'Password must be at least 6 characters.'),
    confirmPassword: z.string().min(1, 'Please confirm your password.'),
  })
  .refine((d) => d.newPassword === d.confirmPassword, {
    message: 'Passwords do not match.',
    path: ['confirmPassword'],
  })

// ── Helpers ──────────────────────────────────────────────────

function getSafeRedirectPath(redirectTo?: string): string {
  if (!redirectTo || redirectTo.startsWith('//')) return '/'
  if (redirectTo.startsWith('/')) return redirectTo
  try {
    const target = new URL(redirectTo)
    if (target.origin !== window.location.origin) return '/'
    return `${target.pathname}${target.search}${target.hash}`
  } catch {
    return '/'
  }
}

// ── Component ────────────────────────────────────────────────

interface UserAuthFormProps extends React.HTMLAttributes<HTMLFormElement> {
  redirectTo?: string
}

export function UserAuthForm({
  className,
  redirectTo,
  ...props
}: UserAuthFormProps) {
  const [isLoading, setIsLoading] = useState(false)
  const [setupToken, setSetupToken] = useState<string | null>(null)
  const navigate = useNavigate()
  const { auth } = useAuthStore()

  // ── Login form ──
  const loginForm = useForm<z.infer<typeof loginSchema>>({
    resolver: zodResolver(loginSchema),
    defaultValues: { username: '', password: '' },
  })

  // ── Setup form ──
  const setupForm = useForm<z.infer<typeof setupSchema>>({
    resolver: zodResolver(setupSchema),
    defaultValues: { newPassword: '', confirmPassword: '' },
  })

  // ── Login submit ──
  async function onLoginSubmit(data: z.infer<typeof loginSchema>) {
    setIsLoading(true)
    try {
      const res = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: data.username, password: data.password }),
      })

      if (res.status === 401) {
        toast.error('Invalid username or password.')
        setIsLoading(false)
        return
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        toast.error(err.detail || 'Login failed.')
        setIsLoading(false)
        return
      }

      const result = await res.json()

      if (result.status === 'SETUP_REQUIRED') {
        // Switch to setup form
        setSetupToken(result.access_token)
        toast.info('First login — please set a new password.')
      } else if (result.status === 'AUTHENTICATED') {
        auth.setAccessToken(result.access_token)
        if (result.user) auth.setUser(result.user)
        navigate({ to: getSafeRedirectPath(redirectTo), replace: true })
        toast.success(`Welcome back, ${data.username}!`)
      }
    } catch {
      toast.error('Network error. Please try again.')
    } finally {
      setIsLoading(false)
    }
  }

  // ── Setup submit ──
  async function onSetupSubmit(data: z.infer<typeof setupSchema>) {
    setIsLoading(true)
    try {
      const res = await fetch('/api/v1/auth/setup', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${setupToken}`,
        },
        body: JSON.stringify({
          new_password: data.newPassword,
          confirm_password: data.confirmPassword,
        }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        toast.error(err.detail || 'Setup failed.')
        setIsLoading(false)
        return
      }

      const result = await res.json()

      if (result.status === 'SETUP_COMPLETE') {
        // Now login with the new password
        const loginRes = await fetch('/api/v1/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: loginForm.getValues('username'),
            password: data.newPassword,
          }),
        })

        if (loginRes.ok) {
          const loginResult = await loginRes.json()
          auth.setAccessToken(loginResult.access_token)
          if (loginResult.user) auth.setUser(loginResult.user)
          navigate({ to: getSafeRedirectPath(redirectTo), replace: true })
          toast.success('Password changed. Welcome to Nova!')
        } else {
          toast.error('Password changed but login failed. Please log in manually.')
          setSetupToken(null)
        }
      }
    } catch {
      toast.error('Network error. Please try again.')
    } finally {
      setIsLoading(false)
    }
  }

  // ── Render: Setup form ───────────────────────────────────
  if (setupToken) {
    return (
      <Form {...setupForm}>
        <form
          onSubmit={setupForm.handleSubmit(onSetupSubmit)}
          className={cn('grid gap-3', className)}
          {...props}
        >
          <div className='rounded-md bg-muted p-3 text-sm text-muted-foreground'>
            First login detected. Please set a new admin password.
          </div>
          <FormField
            control={setupForm.control}
            name='newPassword'
            render={({ field }) => (
              <FormItem>
                <FormLabel>New Password</FormLabel>
                <FormControl>
                  <PasswordInput placeholder='Enter new password' {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={setupForm.control}
            name='confirmPassword'
            render={({ field }) => (
              <FormItem>
                <FormLabel>Confirm Password</FormLabel>
                <FormControl>
                  <PasswordInput placeholder='Confirm new password' {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button className='mt-2' disabled={isLoading}>
            {isLoading ? <Loader2 className='animate-spin' /> : <KeyRound />}
            Set Password & Continue
          </Button>
        </form>
      </Form>
    )
  }

  // ── Render: Login form ───────────────────────────────────
  return (
    <Form {...loginForm}>
      <form
        onSubmit={loginForm.handleSubmit(onLoginSubmit)}
        className={cn('grid gap-3', className)}
        {...props}
      >
        <FormField
          control={loginForm.control}
          name='username'
          render={({ field }) => (
            <FormItem>
              <FormLabel>Username</FormLabel>
              <FormControl>
                <Input placeholder='Enter your username' {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={loginForm.control}
          name='password'
          render={({ field }) => (
            <FormItem>
              <FormLabel>Password</FormLabel>
              <FormControl>
                <PasswordInput placeholder='********' {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button className='mt-2' disabled={isLoading}>
          {isLoading ? <Loader2 className='animate-spin' /> : <LogIn />}
          Sign in
        </Button>
      </form>
    </Form>
  )
}
