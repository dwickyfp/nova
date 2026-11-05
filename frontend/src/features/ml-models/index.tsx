import { useState } from 'react'
import { ChartScatter, Play, Tags } from 'lucide-react'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { PageHeader } from '@/components/ui/page-header'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ModelsTab } from './models-tab'
import { PredictTab } from './predict-tab'
import { AliasesTab } from './aliases-tab'

export function MLModels() {
  const [activeTab, setActiveTab] = useState('models')

  return (
    <>
      <Header fixed>
        <Search />
      </Header>

      <Main scroll>
        <PageHeader
          className='mb-6'
          title='ML Models'
          description='Train classification and regression models from a SQL query, score new rows, and point aliases at the version in production'
        />

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList>
            <TabsTrigger value='models' className='gap-1.5'>
              <ChartScatter className='size-3.5' />
              Models
            </TabsTrigger>
            <TabsTrigger value='predict' className='gap-1.5'>
              <Play className='size-3.5' />
              Predict
            </TabsTrigger>
            <TabsTrigger value='aliases' className='gap-1.5'>
              <Tags className='size-3.5' />
              Aliases
            </TabsTrigger>
          </TabsList>
          <TabsContent value='models' className='mt-4'>
            <ModelsTab />
          </TabsContent>
          <TabsContent value='predict' className='mt-4'>
            <PredictTab />
          </TabsContent>
          <TabsContent value='aliases' className='mt-4'>
            <AliasesTab />
          </TabsContent>
        </Tabs>
      </Main>
    </>
  )
}
