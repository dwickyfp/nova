import { useState } from 'react'
import { Bot, Wand2 } from 'lucide-react'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ProvidersTab } from './providers-tab'
import { FunctionsTab } from './functions-tab'

export function AIProviders() {
  const [activeTab, setActiveTab] = useState('providers')

  return (
    <>
      <Header fixed>
        <Search />
      </Header>

      <Main>
        <div className='mb-2'>
          <h1 className='text-2xl font-semibold tracking-tight'>
            AI Providers
          </h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            Manage LLM provider connections, register models, and configure
            SQL function aliases for AI-powered queries.
          </p>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList>
            <TabsTrigger value='providers' className='gap-1.5'>
              <Bot className='size-3.5' />
              Providers
            </TabsTrigger>
            <TabsTrigger value='functions' className='gap-1.5'>
              <Wand2 className='size-3.5' />
              Functions
            </TabsTrigger>
          </TabsList>
          <TabsContent value='providers' className='mt-4'>
            <ProvidersTab />
          </TabsContent>
          <TabsContent value='functions' className='mt-4'>
            <FunctionsTab />
          </TabsContent>
        </Tabs>
      </Main>
    </>
  )
}
