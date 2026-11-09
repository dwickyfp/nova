import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { PageHeader } from '@/components/ui/page-header'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { passwordPolicyQueryOptions, type SettingsTab, variablesQueryOptions } from './api'
import { PasswordPolicyTab } from './password-policy-tab'
import { VariablesTab } from './variables-tab'

const TABS: { value: SettingsTab; label: string }[] = [
  { value: 'session', label: 'Session variables' },
  { value: 'global', label: 'Global variables' },
  { value: 'password-policy', label: 'Password policy' },
]

export function AdminSettingsPage({
  tab = 'session',
  onTabChange,
}: {
  tab?: SettingsTab
  onTabChange?: (tab: SettingsTab) => void
}) {
  const queryClient = useQueryClient()

  // Every tab's data is small; loading it all in the background makes
  // switching tabs instant.
  useEffect(() => {
    void queryClient.prefetchQuery(variablesQueryOptions('session'))
    void queryClient.prefetchQuery(variablesQueryOptions('global'))
    void queryClient.prefetchQuery(passwordPolicyQueryOptions)
  }, [queryClient])

  return (
    <>
      <Header fixed>
        <Search />
      </Header>

      <Main scroll>
        <div className='min-w-0 space-y-5'>
          <PageHeader
            title='Admin Settings'
            description='Engine variables for your session or the whole engine, and the password policy for Nova sign-ins.'
          />
          <Tabs
            value={tab}
            onValueChange={(value) => onTabChange?.(value as SettingsTab)}
            className='gap-5'
          >
            <TabsList className='h-auto max-w-full shrink-0 justify-start self-start overflow-x-auto [scrollbar-width:none] [&>[role=tab]]:flex-none'>
              {TABS.map((item) => (
                <TabsTrigger key={item.value} value={item.value}>
                  {item.label}
                </TabsTrigger>
              ))}
            </TabsList>
            <TabsContent value='session' className='mt-0 min-w-0'>
              <VariablesTab scope='session' />
            </TabsContent>
            <TabsContent value='global' className='mt-0 min-w-0'>
              <VariablesTab scope='global' />
            </TabsContent>
            <TabsContent value='password-policy' className='mt-0 min-w-0'>
              <PasswordPolicyTab />
            </TabsContent>
          </Tabs>
        </div>
      </Main>
    </>
  )
}
