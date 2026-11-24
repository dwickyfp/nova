import { Logo } from '@/assets/logo'
import { Wordmark } from '@/assets/wordmark'
import { useSearch } from '@tanstack/react-router'
import { SignInVisual } from './components/sign-in-visual'
import { UserAuthForm } from './components/user-auth-form'

export function SignIn2() {
  const { redirect } = useSearch({ from: '/(auth)/sign-in' })
  return (
    <div className='grid h-svh min-h-0 overflow-hidden bg-background lg:grid-cols-2'>
      <main className='relative flex min-h-0 flex-col overflow-y-auto px-6 py-12 sm:px-10 lg:px-16'>
        <div className='mx-auto my-auto w-full max-w-sm'>
          <header className='mb-8'>
            <div className='mb-2 flex items-center gap-3'>
            <Logo sizes='40px'
              className='size-10'
            />
            <h1 className='m-0 text-foreground'>
              <Wordmark
                size={20}
                label='Enterprise Intelligence OS'
                layout='stacked'
                className='gap-1.5'
                labelClassName='font-medium tracking-[0.04em] normal-case'
              />
            </h1>
            </div>
            <h2 className='text-2xl font-semibold tracking-tight'>Sign in to Nova</h2>
          </header>
          <UserAuthForm redirectTo={redirect} />
        </div>
      </main>

      <SignInVisual />
    </div>
  )
}
