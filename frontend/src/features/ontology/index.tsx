import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getRouteApi } from '@tanstack/react-router'
import { BookText, FolderTree, Inbox, ListChecks, Network, PackageCheck } from 'lucide-react'
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
import { useOntologyKey } from './model'
import { ProposalsTab } from './proposals-tab'
import { ReleasesTab } from './releases-tab'

export type OntologyTab = 'concepts' | 'derived' | 'checks' | 'proposals' | 'releases' | 'domains'

const route = getRouteApi('/_authenticated/business-ontology')

/** The routed page: the open tab is kept in the URL. */
export function BusinessOntologyPage() {
  const { tab } = route.useSearch()
  const navigate = route.useNavigate()
  return (
    <BusinessOntology
      tab={tab}
      onTabChange={(next) =>
        navigate({ search: { tab: next === 'concepts' ? undefined : next }, replace: true })
      }
    />
  )
}

export function BusinessOntology({
  tab: initial = 'concepts',
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
            <TabsTrigger value='releases' className='gap-1.5'>
              <PackageCheck className='size-3.5' />
              Releases
            </TabsTrigger>
            <TabsTrigger value='domains' className='gap-1.5'>
              <FolderTree className='size-3.5' />
              Domains
            </TabsTrigger>
          </TabsList>
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
