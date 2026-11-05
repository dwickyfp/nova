import { useState } from 'react'
import { Plug, SquareFunction } from 'lucide-react'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { PageHeader } from '@/components/ui/page-header'
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

      <Main scroll>
        <PageHeader
          className="mb-6"
          title="AI Providers"
          description="Connect model providers, register their models, and map them to SQL functions"
        />

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList>
            <TabsTrigger value="providers" className="gap-1.5">
              <Plug className="size-3.5" />
              Providers
            </TabsTrigger>
            <TabsTrigger value="functions" className="gap-1.5">
              <SquareFunction className="size-3.5" />
              Functions
            </TabsTrigger>
          </TabsList>
          <TabsContent value="providers" className="mt-4">
            <ProvidersTab />
          </TabsContent>
          <TabsContent value="functions" className="mt-4">
            <FunctionsTab />
          </TabsContent>
        </Tabs>
      </Main>
    </>
  )
}
