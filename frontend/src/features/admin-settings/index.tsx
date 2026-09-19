import { useState } from 'react'
import { KeyRound, SlidersHorizontal } from 'lucide-react'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { VariablesTab } from './variables-tab'
import { PasswordPolicyTab } from './password-policy-tab'

export function AdminSettingsPage() {
  const [activeTab, setActiveTab] = useState('variables')

  return (
    <>
      <Header fixed>
        <Search />
      </Header>

      <Main>
        <div className='mb-2'>
          <h1 className='text-2xl font-semibold tracking-tight'>
            Admin Settings
          </h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            Engine session and global variables, plus the password policy that
            governs every Nova login.
          </p>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList>
            <TabsTrigger value='variables' className='gap-1.5'>
              <SlidersHorizontal className='size-3.5' />
              Variables
            </TabsTrigger>
            <TabsTrigger value='password-policy' className='gap-1.5'>
              <KeyRound className='size-3.5' />
              Password policy
            </TabsTrigger>
          </TabsList>
          <TabsContent value='variables' className='mt-4'>
            <VariablesTab />
          </TabsContent>
          <TabsContent value='password-policy' className='mt-4'>
            <PasswordPolicyTab />
          </TabsContent>
        </Tabs>
      </Main>
    </>
  )
}
