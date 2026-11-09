import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getRouteApi } from '@tanstack/react-router'
import { z } from 'zod'
import { useNevaSurface } from '@/features/assistant/neva-surface-hook'
import { defineNevaCapability } from '@/features/assistant/surface-registry'
import {
  BookText,
  FlaskConical,
  FolderTree,
  Gauge,
  Inbox,
  ListChecks,
  Network,
  PackageCheck,
} from 'lucide-react'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { Search } from '@/components/search'
import { PageHeader } from '@/components/ui/page-header'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ontologyApi } from './api'
import { ChecksTab } from './checks-tab'
import { ConceptsTab } from './concepts-tab'
import { DerivedTab } from './derived-tab'
import { DomainsTab } from './domains-tab'
import { EvaluationTab } from './evaluation-tab'
import { useOntologyKey } from './model'
import { OverviewTab } from './overview-tab'
import { ProposalsTab } from './proposals-tab'
import { ReleasesTab } from './releases-tab'

export type OntologyTab =
  | 'overview'
  | 'concepts'
  | 'derived'
  | 'checks'
  | 'proposals'
  | 'evaluation'
  | 'releases'
  | 'domains'

const ONTOLOGY_TABS = [
  'overview', 'concepts', 'derived', 'checks', 'proposals', 'evaluation', 'releases', 'domains',
] as const satisfies readonly OntologyTab[]

const route = getRouteApi('/_authenticated/business-ontology')

/** The routed page: the open tab is kept in the URL. */
export function BusinessOntologyPage() {
  const { tab } = route.useSearch()
  const navigate = route.useNavigate()
  return (
    <BusinessOntology
      tab={tab}
      onTabChange={(next) =>
        navigate({ search: { tab: next === 'overview' ? undefined : next }, replace: true })
      }
    />
  )
}

export function BusinessOntology({
  tab: initial = 'overview',
  onTabChange,
}: {
  tab?: OntologyTab
  onTabChange?: (tab: OntologyTab) => void
}) {
  const [tab, setTab] = useState<OntologyTab>(initial)
  const pending = useQuery({
    queryKey: useOntologyKey('proposals', 'all'),
    queryFn: () => ontologyApi.proposals(),
  })
  const inReview = (pending.data ?? []).filter((item) => item.status === 'PENDING').length
  const change = (value: string) => {
    setTab(value as OntologyTab)
    onTabChange?.(value as OntologyTab)
  }
  useNevaSurface({
    id: 'business_ontology',
    route: '/business-ontology',
    title: 'Business Ontology',
    context: () => ({ view: { activeTab: tab, filters: { proposalsInReview: inReview } } }),
    capabilities: [
      defineNevaCapability({
        name: 'tab.open', risk: 'safe',
        argsSchema: z.object({ tab: z.enum(ONTOLOGY_TABS) }),
        execute: ({ tab: next }) => change(next),
      }),
      defineNevaCapability({
        name: 'surface.refresh', risk: 'safe', argsSchema: z.object({}),
        execute: () => pending.refetch({ throwOnError: true }),
      }),
    ],
    suggestedActions: [
      { label: 'What does a term mean', prompt: 'What does net revenue officially mean?' },
      { label: 'My suggestions', prompt: 'Were my definition suggestions approved?' },
    ],
  })

  return (
    <>
      <Header fixed>
        <Search />
      </Header>
      <Main scroll>
        <PageHeader
          className='mb-6'
          title='Business Ontology'
          description='The official meaning of business terms: what each concept means, who owns it, and which governed metric computes it. Studio answers with these definitions.'
        />
        <Tabs value={tab} onValueChange={change}>
          <TabsList className='max-w-full overflow-x-auto'>
            <TabsTrigger value='overview' className='gap-1.5'>
              <Gauge className='size-3.5' />
              Overview
            </TabsTrigger>
            <TabsTrigger value='concepts' className='gap-1.5'>
              <BookText className='size-3.5' />
              Concepts
            </TabsTrigger>
            <TabsTrigger value='derived' className='gap-1.5'>
              <Network className='size-3.5' />
              Derived
            </TabsTrigger>
            <TabsTrigger value='checks' className='gap-1.5'>
              <ListChecks className='size-3.5' />
              Checks
            </TabsTrigger>
            <TabsTrigger value='proposals' className='gap-1.5'>
              <Inbox className='size-3.5' />
              Proposals
              {inReview ? (
                <span className='text-xs text-muted-foreground'>{inReview}</span>
              ) : null}
            </TabsTrigger>
            <TabsTrigger value='evaluation' className='gap-1.5'>
              <FlaskConical className='size-3.5' />
              Evaluation
            </TabsTrigger>
            <TabsTrigger value='releases' className='gap-1.5'>
              <PackageCheck className='size-3.5' />
              Releases
            </TabsTrigger>
            <TabsTrigger value='domains' className='gap-1.5'>
              <FolderTree className='size-3.5' />
              Domains
            </TabsTrigger>
          </TabsList>
          <TabsContent value='overview' className='mt-4'>
            <OverviewTab onOpen={change} />
          </TabsContent>
          <TabsContent value='concepts' className='mt-4'>
            <ConceptsTab />
          </TabsContent>
          <TabsContent value='derived' className='mt-4'>
            <DerivedTab />
          </TabsContent>
          <TabsContent value='checks' className='mt-4'>
            <ChecksTab />
          </TabsContent>
          <TabsContent value='proposals' className='mt-4'>
            <ProposalsTab />
          </TabsContent>
          <TabsContent value='evaluation' className='mt-4'>
            <EvaluationTab />
          </TabsContent>
          <TabsContent value='releases' className='mt-4'>
            <ReleasesTab />
          </TabsContent>
          <TabsContent value='domains' className='mt-4'>
            <DomainsTab />
          </TabsContent>
        </Tabs>
      </Main>
    </>
  )
}
