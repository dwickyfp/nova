import { useDeferredValue, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BookText, Search as SearchIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { LoadingLines } from '@/components/ui/loading-overlay'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { StatusBadge } from '@/components/ui/status-badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ontologyApi, type Scope } from './api'
import { ConceptSheet } from './concept-detail'
import { KIND_LABELS, useDomains, useOntologyKey } from './model'
import { BootstrapDialog, ConceptDialog } from './proposal-forms'

const ALL = '__all__'

export function ConceptsTab() {
  const [scope, setScope] = useState<Scope>('current')
  const [domainId, setDomainId] = useState(ALL)
  const [search, setSearch] = useState('')
  const [showDeprecated, setShowDeprecated] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [proposing, setProposing] = useState(false)
  const [drafting, setDrafting] = useState(false)
  const query = useDeferredValue(search.trim())
  const domains = useDomains()
  const concepts = useQuery({
    queryKey: useOntologyKey('concepts', scope, domainId, query, showDeprecated),
    queryFn: () =>
      ontologyApi.concepts({
        scope,
        domainId: domainId === ALL ? undefined : domainId,
        q: query || undefined,
        includeDeprecated: showDeprecated,
      }),
  })
  const list = concepts.data?.concepts ?? []
  const filtered = Boolean(query) || domainId !== ALL

  return (
    <div className='flex flex-col gap-4'>
      <div className='flex flex-wrap items-center gap-2'>
        <Tabs value={scope} onValueChange={(value) => setScope(value as Scope)}>
          <TabsList aria-label='Which definitions to show'>
            <TabsTrigger value='current'>Published</TabsTrigger>
            <TabsTrigger value='working'>Latest approved</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className='relative min-w-48 flex-1 sm:max-w-72'>
          <SearchIcon
            aria-hidden
            className='pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground'
          />
          <Input
            aria-label='Search concepts'
            placeholder='Name, label or other name'
            className='pl-8'
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <Select value={domainId} onValueChange={setDomainId}>
          <SelectTrigger aria-label='Domain' className='w-44'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All domains</SelectItem>
            {(domains.data ?? []).map((item) => (
              <SelectItem key={item.id} value={item.id}>
                {item.qualified_path}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {scope === 'working' ? (
          <div className='flex items-center gap-2'>
            <Checkbox
              id='show-deprecated'
              checked={showDeprecated}
              onCheckedChange={(value) => setShowDeprecated(value === true)}
            />
            <Label htmlFor='show-deprecated' className='font-normal text-muted-foreground'>
              Show deprecated
            </Label>
          </div>
        ) : null}
        <div className='flex w-full flex-wrap gap-2 sm:ml-auto sm:w-auto'>
          <Button variant='outline' onClick={() => setDrafting(true)}>
            Draft from a Semantic View
          </Button>
          <Button onClick={() => setProposing(true)}>Propose a concept</Button>
        </div>
      </div>

      <p className='text-xs text-muted-foreground'>
        {scope === 'current'
          ? concepts.data?.release_id
            ? `Release ${concepts.data.release_id}: the definitions Studio uses now.`
            : 'The definitions Studio uses now.'
          : 'Approved definitions, including those not yet in a published release.'}
      </p>

      {concepts.isLoading ? (
        <LoadingLines rows={6} />
      ) : concepts.isError ? (
        <EmptyState
          variant='error'
          icon={BookText}
          title='Concepts could not be loaded'
          description={concepts.error.message}
          action={<Button variant='outline' onClick={() => concepts.refetch()}>Try again</Button>}
        />
      ) : list.length === 0 ? (
        filtered ? (
          <EmptyState
            icon={SearchIcon}
            title='No concept matches'
            description='Try another name, or choose all domains.'
          />
        ) : scope === 'current' && !concepts.data?.release_id ? (
          <EmptyState
            icon={BookText}
            title='No release is published yet'
            description='Studio uses business definitions once a release is published. Approved definitions wait under Latest approved.'
            action={<Button variant='outline' onClick={() => setScope('working')}>Show latest approved</Button>}
          />
        ) : (
          <EmptyState
            icon={BookText}
            title='No concepts you can see'
            description='Propose the first definition for a domain, or draft them from a published Semantic View.'
            action={<Button onClick={() => setProposing(true)}>Propose a concept</Button>}
          />
        )
      ) : (
        <div className='overflow-x-auto rounded-md border'>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Concept</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead className='hidden md:table-cell'>Unit</TableHead>
                <TableHead className='hidden lg:table-cell'>Other names</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.map((concept) => (
                <TableRow key={concept.id}>
                  <TableCell className='max-w-96'>
                    <button
                      type='button'
                      className='w-full rounded-sm text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
                      onClick={() => setSelected(concept.id)}
                    >
                      <span className='block truncate font-medium'>{concept.qualified_name}</span>
                      <span className='block truncate text-xs text-muted-foreground'>
                        {concept.label}
                      </span>
                    </button>
                  </TableCell>
                  <TableCell>
                    <span className='flex items-center gap-2'>
                      {KIND_LABELS[concept.kind]}
                      {concept.status === 'DEPRECATED' ? (
                        <StatusBadge tone='neutral'>Deprecated</StatusBadge>
                      ) : null}
                    </span>
                  </TableCell>
                  <TableCell className='hidden md:table-cell'>{concept.unit ?? ''}</TableCell>
                  <TableCell className='hidden max-w-64 truncate text-muted-foreground lg:table-cell'>
                    {concept.aliases.join(', ')}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {concepts.data?.truncated ? (
        <p className='text-xs text-muted-foreground'>
          Showing the first {list.length} concepts. Search or choose a domain to narrow the list.
        </p>
      ) : null}

      <ConceptSheet conceptId={selected} scope={scope} onClose={() => setSelected(null)} />
      <ConceptDialog open={proposing} onOpenChange={setProposing} />
      <BootstrapDialog open={drafting} onOpenChange={setDrafting} />
    </div>
  )
}
