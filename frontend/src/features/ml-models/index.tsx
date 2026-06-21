import { useState } from 'react'
import { Brain, Play, Tags } from 'lucide-react'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
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

      <Main>
        <div className='mb-2'>
          <h1 className='text-2xl font-semibold tracking-tight'>
            ML Models
          </h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            Train classical ML models from SQL queries, run predictions, and
            manage model aliases for production deployments.
          </p>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList>
            <TabsTrigger value='models' className='gap-1.5'>
              <Brain className='size-3.5' />
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
