import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render } from 'vitest-browser-react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth-store'
import { semanticViewsApi } from '@/features/intelligence/semantic-views-api'
import {
  ontologyApi,
  type Concept,
  type ConceptList,
  type Domain,
  type Proposal,
  type ReleaseSummary,
} from './api'
import { ConceptsTab } from './concepts-tab'
import { DomainsTab } from './domains-tab'
import { conceptNames, describeChange, splitList } from './model'
import { ProposalsTab } from './proposals-tab'
import { ReleasesTab } from './releases-tab'
import '@/styles/index.css'

const finance: Domain = {
  id: 'dom_fin',
  revision: 1,
  name: 'Finance',
  qualified_path: 'Finance',
  parent_id: null,
  description: 'Money',
  steward_roles: ['finance_steward'],
  visibility_roles: [],
  status: 'ACTIVE',
  can_review: true,
  updated_at: '2026-11-06T00:00:00Z',
}

const revenue: Concept = {
  id: 'con_rev',
  revision: 2,
  qualified_name: 'Finance.NetRevenue',
  domain_id: 'dom_fin',
  kind: 'METRIC',
  slug: 'NetRevenue',
  label: 'Net revenue',
  definition: 'Revenue after discounts and refunds.',
  aliases: ['omzet'],
  unit: 'IDR',
  grain: 'Order per day',
  time_semantics: null,
  exclusions: ['Intercompany sales'],
  classification: 'INTERNAL',
  status: 'APPROVED',
  proposal_id: 'prp_1',
  updated_at: '2026-11-06T00:00:00Z',
}

function list(scope: 'current' | 'working', concepts: Concept[], releaseId: string | null): ConceptList {
  return { scope, release_id: releaseId, manifest_hash: null, concepts, truncated: false }
}

function release(overrides: Partial<ReleaseSummary> = {}): ReleaseSummary {
  return {
    id: 'ont_rel_0001',
    revision: 1,
    sequence: 1,
    base_release_id: null,
    manifest_hash: 'a'.repeat(64),
    status: 'PUBLISHED',
    note: 'Pilot',
    published_at: '2026-11-06T00:00:00Z',
    created_at: '2026-11-06T00:00:00Z',
    actor: { principal: 'nova_admin', active_role: 'ACCOUNTADMIN' },
    member_counts: { concept: 1, representation: 1, domain: 1 },
    ...overrides,
  }
}

function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: 'prp_2',
    revision: 1,
    title: 'Add Finance.Churn',
    rationale: 'Pilot scope',
    source: 'USER',
    base_release_id: null,
    changes: [
      {
        target: 'concept',
        op: 'create',
        concept_id: 'con_new',
        body: { ...revenue, slug: 'Churn', label: 'Churn', kind: 'METRIC' },
      },
      { target: 'representation', op: 'create', concept_id: 'con_new', view_id: 'sales', metric: 'churn' },
    ],
    domain_ids: ['dom_fin'],
    submitter: 'ana',
    operation_id: 'op',
    status: 'PENDING',
    reviews: [],
    created_at: '2026-11-06T00:00:00Z',
    updated_at: '2026-11-06T00:00:00Z',
    ...overrides,
  }
}

function signIn(username: string, activeRole: string) {
  useAuthStore.setState((state) => ({
    ...state,
    auth: { ...state.auth, user: { username, roles: [activeRole], activeRole } },
  }))
}

function mount(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)
}

beforeEach(() => {
  vi.spyOn(ontologyApi, 'domains').mockResolvedValue([finance])
})

afterEach(async () => {
  await cleanup()
  vi.restoreAllMocks()
})

it('explains an unpublished ontology and shows approved definitions on request', async () => {
  signIn('fiona', 'finance_steward')
  const concepts = vi
    .spyOn(ontologyApi, 'concepts')
    .mockImplementation(async ({ scope }) =>
      scope === 'current' ? list('current', [], null) : list('working', [revenue], null)
    )
  const screen = await mount(<ConceptsTab />)

  await expect.element(screen.getByText('No release is published yet')).toBeVisible()
  await screen.getByRole('button', { name: 'Show latest approved' }).click()
  await expect.element(screen.getByText('Finance.NetRevenue')).toBeVisible()
  expect(concepts).toHaveBeenLastCalledWith(expect.objectContaining({ scope: 'working' }))
})

it('opens a concept with its definition, binding and history', async () => {
  signIn('ana', 'finance_analyst')
  vi.spyOn(ontologyApi, 'concepts').mockResolvedValue(list('current', [revenue], 'ont_rel_0001'))
  vi.spyOn(ontologyApi, 'concept').mockResolvedValue({
    scope: 'current',
    release_id: 'ont_rel_0001',
    manifest_hash: null,
    concept: revenue,
    history: [
      { revision: 2, status: 'APPROVED', label: 'Net revenue', proposal_id: 'p2', changed_by: 'fiona', updated_at: revenue.updated_at },
      { revision: 1, status: 'APPROVED', label: 'Revenue', proposal_id: 'p1', changed_by: 'fiona', updated_at: revenue.updated_at },
    ],
  })
  vi.spyOn(ontologyApi, 'bindings').mockResolvedValue({
    scope: 'current',
    release_id: 'ont_rel_0001',
    manifest_hash: null,
    representations: [
      {
        id: 'rep_1',
        revision: 1,
        semantic: { view_id: 'sales', version: 3, fingerprint: 'f' },
        metric: 'total_revenue',
        purpose: 'general',
        unit: 'IDR',
        status: 'STALE',
        status_reason: 'Version 4 changed the definition of total_revenue',
        usable: false,
        validated_at: revenue.updated_at,
      },
    ],
  })
  vi.spyOn(ontologyApi, 'related').mockResolvedValue({
    scope: 'current', release_id: null, manifest_hash: null, concepts: [], relations: [],
  })
  vi.spyOn(ontologyApi, 'proposals').mockResolvedValue([])
  const screen = await mount(<ConceptsTab />)

  await expect.element(screen.getByText('Release ont_rel_0001', { exact: false })).toBeVisible()
  await screen.getByRole('button', { name: /Finance\.NetRevenue/ }).click()
  await expect.element(screen.getByText('Revenue after discounts and refunds.')).toBeVisible()
  await expect.element(screen.getByText('sales.total_revenue')).toBeVisible()
  await expect.element(screen.getByText('Needs review')).toBeVisible()
  await expect.element(screen.getByText('Version 4 changed the definition of total_revenue')).toBeVisible()
  await expect.element(screen.getByText('Version 1: Revenue')).toBeVisible()
})

it('proposes a concept bound to a published metric', async () => {
  signIn('ana', 'finance_analyst')
  vi.spyOn(ontologyApi, 'concepts').mockResolvedValue(list('current', [revenue], 'ont_rel_0001'))
  vi.spyOn(semanticViewsApi, 'list').mockResolvedValue([
    { id: 'sales', name: 'sales', active_version: 2 } as never,
  ])
  vi.spyOn(semanticViewsApi, 'get').mockResolvedValue({
    id: 'sales',
    active_version: 2,
    versions: [
      {
        version: 2,
        definition: { metrics: [{ name: 'churned_accounts', unit: 'accounts' }] },
      },
    ],
  } as never)
  const propose = vi.spyOn(ontologyApi, 'propose').mockResolvedValue(proposal())
  const screen = await mount(<ConceptsTab />)

  await screen.getByRole('button', { name: 'Propose a concept' }).click()
  await screen.getByRole('combobox', { name: 'Domain' }).click()
  await screen.getByRole('option', { name: 'Finance' }).click()
  await screen.getByLabelText('Name', { exact: true }).fill('Churn')
  await screen.getByLabelText('Label', { exact: true }).fill('Churned accounts')
  await screen.getByLabelText('Definition', { exact: true }).fill('Accounts that ended their contract.')
  await screen.getByLabelText('Other names', { exact: true }).fill('churn, attrition')
  await screen.getByRole('combobox', { name: 'Semantic View' }).click()
  await screen.getByRole('option', { name: 'sales' }).click()
  await screen.getByRole('combobox', { name: 'Metric' }).click()
  await screen.getByRole('option', { name: 'churned_accounts (accounts)' }).click()
  await screen.getByLabelText('Why this change', { exact: true }).fill('Customer Health pilot')
  await screen.getByRole('button', { name: 'Send for review' }).click()

  await expect.poll(() => propose.mock.calls.length).toBe(1)
  const body = propose.mock.calls[0][0]
  expect(body.title).toBe('Add Finance.Churn')
  expect(body.changes[0]).toMatchObject({
    target: 'concept',
    op: 'create',
    body: { domain_id: 'dom_fin', slug: 'Churn', aliases: ['churn', 'attrition'], kind: 'METRIC' },
  })
  expect(body.changes[1]).toEqual({
    target: 'representation',
    op: 'create',
    concept_id: '@0',
    view_id: 'sales',
    metric: 'churned_accounts',
  })
  expect(body.operation_id).toMatch(/^[0-9a-f-]{36}$/)
})

it('lets a steward approve a proposal and keeps the submitter from reviewing', async () => {
  signIn('fiona', 'finance_steward')
  vi.spyOn(ontologyApi, 'proposals').mockResolvedValue([proposal()])
  vi.spyOn(ontologyApi, 'concepts').mockResolvedValue(list('working', [revenue], null))
  const review = vi
    .spyOn(ontologyApi, 'review')
    .mockResolvedValue(proposal({ status: 'APPLIED' }))
  const screen = await mount(<ProposalsTab />)

  await expect.element(screen.getByText('Add metric Finance.Churn')).toBeVisible()
  await expect.element(screen.getByText('Compute Finance.Churn with sales metric churn')).toBeVisible()
  await expect.element(screen.getByText('Waiting for a steward of:', { exact: false })).toBeVisible()
  await screen.getByRole('button', { name: 'Approve' }).click()
  await screen.getByLabelText('What you checked', { exact: true }).fill('Definition matches the contract')
  await screen.getByRole('dialog').getByRole('button', { name: 'Approve' }).click()
  await expect.poll(() => review.mock.calls.length).toBe(1)
  expect(review.mock.calls[0][1]).toMatchObject({
    decision: 'APPROVE',
    rationale: 'Definition matches the contract',
  })

  await cleanup()
  signIn('ana', 'finance_analyst')
  const own = await mount(<ProposalsTab />)
  await expect.element(own.getByText('Another steward reviews your proposal.')).toBeVisible()
  expect(own.getByRole('button', { name: 'Approve' }).query()).toBeNull()
  await expect.element(own.getByRole('button', { name: 'Withdraw' })).toBeVisible()
})

it('publishes a prepared release and refuses one prepared before the current release', async () => {
  signIn('nova_admin', 'ACCOUNTADMIN')
  vi.spyOn(ontologyApi, 'currentRelease').mockResolvedValue({
    release: release(),
    previous_release_id: null,
  })
  vi.spyOn(ontologyApi, 'releases').mockResolvedValue([
    release({ id: 'ont_rel_0003', sequence: 3, status: 'PREPARED', base_release_id: 'ont_rel_0001', published_at: null, warnings: ['Nothing changed since the current release'] }),
    release({ id: 'ont_rel_0002', sequence: 2, status: 'PREPARED', base_release_id: null, published_at: null }),
    release(),
  ])
  const publish = vi
    .spyOn(ontologyApi, 'publishRelease')
    .mockResolvedValue(release({ id: 'ont_rel_0003', sequence: 3 }))
  const screen = await mount(<ReleasesTab />)

  await expect.element(screen.getByText('Nothing changed since the current release')).toBeVisible()
  const publishButtons = screen.getByRole('button', { name: 'Publish' })
  await expect.element(publishButtons.nth(1)).toBeDisabled()
  await expect
    .element(screen.getByText('Another release was published after this one was prepared', { exact: false }))
    .toBeVisible()
  await publishButtons.nth(0).click()
  await expect.poll(() => publish.mock.calls[0]?.[0]).toBe('ont_rel_0003')
})

it('shows releases read-only to a non-administrator', async () => {
  signIn('ana', 'finance_analyst')
  vi.spyOn(ontologyApi, 'currentRelease').mockResolvedValue({ release: null, previous_release_id: null })
  vi.spyOn(ontologyApi, 'releases').mockResolvedValue([])
  const screen = await mount(<ReleasesTab />)

  await expect.element(screen.getByText('No release is published', { exact: false })).toBeVisible()
  await expect.element(screen.getByText('ACCOUNTADMIN prepares and publishes releases', { exact: false })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Prepare a release' }).query()).toBeNull()
})

it('creates a domain with steward and reader roles', async () => {
  signIn('nova_admin', 'ACCOUNTADMIN')
  const create = vi.spyOn(ontologyApi, 'createDomain').mockResolvedValue({ ...finance, id: 'dom_sales', qualified_path: 'Sales' })
  const screen = await mount(<DomainsTab />)

  await expect.element(screen.getByText('(your role)')).toBeVisible()
  await screen.getByRole('button', { name: 'New domain' }).click()
  await screen.getByLabelText('Name', { exact: true }).fill('Sales')
  await screen.getByLabelText('Steward roles', { exact: true }).fill('sales_steward, sales_ops')
  await screen.getByLabelText('Reader roles', { exact: true }).fill('sales_analyst')
  await screen.getByRole('button', { name: 'Save' }).click()
  await expect.poll(() => create.mock.calls.length).toBe(1)
  expect(create.mock.calls[0][0]).toEqual({
    name: 'Sales',
    parent_id: null,
    description: '',
    steward_roles: ['sales_steward', 'sales_ops'],
    visibility_roles: ['sales_analyst'],
  })
})

it('describes proposed changes with the names a reader can see', () => {
  const names = conceptNames(proposal().changes, [revenue], new Map([[finance.id, finance]]))
  expect(names.get('con_new')).toBe('Finance.Churn')
  expect(
    describeChange(
      { target: 'relation', op: 'create', source_id: 'con_new', type: 'MEASURES', target_id: 'con_hidden' },
      names
    )
  ).toBe('Link Finance.Churn measures a concept you cannot see')
  expect(describeChange({ target: 'concept', op: 'deprecate', concept_id: 'con_rev' }, names)).toBe(
    'Deprecate Finance.NetRevenue'
  )
  expect(splitList(' a, b\n c ,, ')).toEqual(['a', 'b', 'c'])
})

it('explains why there is nothing to draft from when no view is published', async () => {
  signIn('ana', 'finance_analyst')
  vi.spyOn(ontologyApi, 'concepts').mockResolvedValue(list('current', [], null))
  vi.spyOn(semanticViewsApi, 'list').mockResolvedValue([])
  const screen = await mount(<ConceptsTab />)

  await screen.getByRole('button', { name: 'Draft from a Semantic View' }).click()
  await expect
    .element(screen.getByText('No published Semantic View is available to your role', { exact: false }))
    .toBeVisible()
  await expect.element(screen.getByRole('button', { name: 'Draft concepts' })).toBeDisabled()
})
