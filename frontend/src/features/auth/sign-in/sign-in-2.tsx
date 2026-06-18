import { useSearch } from '@tanstack/react-router'
import { SignInVisual } from './components/sign-in-visual'
import { UserAuthForm } from './components/user-auth-form'

export function SignIn2() {
  const { redirect } = useSearch({ from: '/(auth)/sign-in' })
  return (
    <div className='grid min-h-svh bg-background lg:grid-cols-2'>
      <main className='relative flex min-h-svh items-center justify-center px-6 py-12 sm:px-10 lg:px-16'>
        <div className='w-full max-w-sm'>
          <div className='mb-2 flex items-center gap-3'>
            <img
              src='/images/nova-mark.svg'
              alt=''
              className='size-10'
              aria-hidden='true'
            />
            <div className='leading-tight'>
              <h1 className='text-lg font-bold tracking-tight'>Nova</h1>
              <p className='text-xs font-medium text-muted-foreground'>
                Powered by StarRocks
              </p>
            </div>
          </div>

          <div className='flex flex-col space-y-2 text-start mb-8'>
            <h2 className='text-2xl font-semibold tracking-tight'>
              Sign in to Nova
            </h2>
          </div>
          <UserAuthForm redirectTo={redirect} />
        </div>
      </main>

      <SignInVisual />
    </div>
  )
}
