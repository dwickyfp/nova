import { useState } from 'react'
import { KeyRound, SlidersHorizontal } from 'lucide-react'
import { PageHeader } from '@/components/ui/page-header'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { VariablesTab } from './variables-tab'
import { PasswordPolicyTab } from './password-policy-tab'

export function AdminSettingsPage() {
  const [activeTab, setActiveTab] = useState('variables')

  return (
    <div className='space-y-6'>
      <PageHeader
        title='Admin Settings'
        description='Engine session and global variables, plus the password policy that governs every Nova login.'
      />

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
    </div>
  )
}
