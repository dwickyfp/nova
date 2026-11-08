import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render } from 'vitest-browser-react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth-store'
import { semanticViewsApi } from '@/features/intelligence/semantic-views-api'
import {
  ontologyApi,
  type Concept,
  type ConceptList,
  type DerivedItem,
  type Domain,
  type OntologyMetrics,
  type Proposal,
  type ReleaseSummary,
} from './api'
import { ChecksTab } from './checks-tab'
import { ConceptsTab } from './concepts-tab'
import { DerivedTab } from './derived-tab'
import { DomainsTab } from './domains-tab'
import { EvaluationTab } from './evaluation-tab'
import { OverviewTab } from './overview-tab'
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
  vi.spyOn(semanticViewsApi, 'list').mockResolvedValue([])
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

function derived(overrides: Partial<DerivedItem>): DerivedItem {
  return {
    id: overrides.key ?? 'drv',
    key: 'entity:retail.employees',
    item_type: 'concept',
    concept_kind: null,
    label: '',
    definition: '',
    aliases: [],
    unit: null,
    is_time: false,
    metric: null,
    relation_type: null,
    source_key: null,
    target_key: null,
    role: null,
    roles: [],
    official_concept: null,
    confidence: 0.9,
    evidence: [
      {
        semantic: { view_id: 'retail_sales', version: 3, fingerprint: 'f' },
        view_name: 'retail_sales',
        method: 'dataset',
        source: 'dataset:sales_orders',
        confidence: 0.9,
      },
    ],
    ...overrides,
  }
}

it('shows derived entities with roles, official links and how metrics are built', async () => {
  const items: DerivedItem[] = [
    derived({ key: 'entity:retail.employees', concept_kind: 'BUSINESS_ENTITY', label: 'Employee', roles: ['driver', 'salesman'] }),
    derived({ key: 'entity:retail.sales_orders', concept_kind: 'BUSINESS_ENTITY', label: 'Sales order' }),
    derived({ key: 'metric:v.net_revenue', concept_kind: 'METRIC', label: 'Net revenue', unit: 'IDR', official_concept: 'Penjualan.NetRevenue' }),
    derived({ key: 'metric:v.returned_value', concept_kind: 'METRIC', label: 'Returned value' }),
    derived({ key: 'rel:m', item_type: 'relation', relation_type: 'MEASURES', source_key: 'metric:v.net_revenue', target_key: 'entity:retail.sales_orders' }),
    derived({ key: 'rel:d', item_type: 'relation', relation_type: 'DERIVES', source_key: 'metric:v.returned_value', target_key: 'metric:v.net_revenue' }),
    derived({ key: 'dimension:retail.employees.city', concept_kind: 'DIMENSION', label: 'City' }),
    derived({ key: 'rel:c', item_type: 'relation', relation_type: 'RELATED_TO', source_key: 'dimension:retail.employees.city', target_key: 'entity:retail.employees' }),
    derived({ key: 'rel:r', item_type: 'relation', relation_type: 'RELATED_TO', source_key: 'entity:retail.sales_orders', target_key: 'entity:retail.employees', role: 'salesman' }),
  ]
  vi.spyOn(ontologyApi, 'derived').mockResolvedValue({ items, compilations: [], compiler_version: 'nova-derived:1' })
  const screen = await mount(<DerivedTab />)

  await expect.element(screen.getByText('driver, salesman')).toBeVisible()
  await screen.getByRole('button', { name: 'Sales order', exact: true }).click()
  await expect.element(screen.getByText('Official: Penjualan.NetRevenue')).toBeVisible()
  await expect.element(screen.getByText('Built from Returned value')).toBeVisible()
  await expect.element(screen.getByText(/as its salesman/)).toBeVisible()
})

it('offers to compile when nothing is derived yet', async () => {
  vi.spyOn(ontologyApi, 'derived').mockResolvedValue({ items: [], compilations: [], compiler_version: 'nova-derived:1' })
  const compile = vi.spyOn(ontologyApi, 'compileDerived').mockResolvedValue({ compilations: [] })
  const screen = await mount(<DerivedTab />)
  await expect.element(screen.getByText('Nothing derived yet')).toBeVisible()
  await screen.getByRole('button', { name: 'Compile now' }).click()
  expect(compile).toHaveBeenCalledOnce()
})

it('drafts proposals per domain from the derived layer and says what it left out', async () => {
  const items: DerivedItem[] = [
    derived({ key: 'entity:retail.outlets', concept_kind: 'BUSINESS_ENTITY', label: 'Outlet' }),
  ]
  vi.spyOn(ontologyApi, 'derived').mockResolvedValue({ items, compilations: [], compiler_version: 'nova-derived:2' })
  const propose = vi.spyOn(ontologyApi, 'proposeDerived').mockResolvedValue({
    proposals: [proposal({ id: 'prp_e', title: 'Derived meaning for Finance', source: 'ENGINE' })],
    skipped: [{ key: 'entity:retail.outlets', label: 'Outlet', reason: 'Several domains use this entity; choose a domain for shared entities' }],
  })
  const screen = await mount(<DerivedTab />)
  await screen.getByRole('button', { name: 'Propose to stewards' }).click()
  await screen.getByRole('button', { name: 'Draft proposals' }).click()
  expect(propose).toHaveBeenCalledWith(expect.objectContaining({ shared_domain_id: null }))
  await expect.element(screen.getByText('1 proposal sent for review')).toBeVisible()
  await expect.element(screen.getByText('Derived meaning for Finance')).toBeVisible()
  await expect.element(screen.getByText(/Several domains use this entity/)).toBeVisible()
})

it('labels engine proposals and shows why each link was proposed', async () => {
  signIn('sam', 'finance_steward')
  vi.spyOn(ontologyApi, 'concepts').mockResolvedValue(list('working', [revenue], null))
  vi.spyOn(ontologyApi, 'proposals').mockResolvedValue([
    proposal({
      source: 'ENGINE',
      changes: [
        { target: 'relation', op: 'create', source_id: 'con_rev', type: 'DERIVES', target_id: 'con_rev', justification: 'Derived by Nova (95%) from retail_sales v3 metric:net_revenue' },
      ],
    }),
  ])
  const screen = await mount(<ProposalsTab />)
  await expect.element(screen.getByText(/Derived by Nova, sent by ana/)).toBeVisible()
  await expect.element(screen.getByText(/from retail_sales v3 metric:net_revenue/)).toBeVisible()
})

it('names the Semantic View a proposed binding uses', () => {
  const names = conceptNames([], [revenue], new Map(), [{ id: 'v-uuid', name: 'retail_sales' }])
  expect(
    describeChange({ target: 'representation', op: 'create', concept_id: 'con_rev', view_id: 'v-uuid', metric: 'net_revenue' }, names)
  ).toBe('Compute Finance.NetRevenue with retail_sales metric net_revenue')
})

it('lists meaning checks with their evidence', async () => {
  vi.spyOn(ontologyApi, 'lint').mockResolvedValue({
    counts: { error: 1, warning: 0 },
    findings: [
      {
        rule: 'definition_formula',
        severity: 'error',
        concept_id: 'con_gm',
        concept: 'Penjualan.GrossMargin',
        message: 'The definition names Net revenue, but the formula leaves out Returned value.',
        evidence: ['definition: Omzet bersih dikurangi harga pokok.'],
      },
    ],
  })
  const screen = await mount(<ChecksTab />)
  await expect.element(screen.getByText('Penjualan.GrossMargin')).toBeVisible()
  await expect.element(screen.getByText('Mismatch')).toBeVisible()
  await expect.element(screen.getByText('Definition and formula disagree')).toBeVisible()
  await expect.element(screen.getByText(/Omzet bersih dikurangi/)).toBeVisible()
})

it('says so when every definition matches', async () => {
  vi.spyOn(ontologyApi, 'lint').mockResolvedValue({ counts: { error: 0, warning: 0 }, findings: [] })
  const screen = await mount(<ChecksTab />)
  await expect.element(screen.getByText('No meaning problems found')).toBeVisible()
})

it('suggests names and shows what the validator dropped', async () => {
  vi.spyOn(ontologyApi, 'lint').mockResolvedValue({ counts: { error: 0, warning: 0 }, findings: [] })
  const enrich = vi.spyOn(ontologyApi, 'enrich').mockResolvedValue({
    proposals: [proposal({ id: 'prp_n', title: 'Names and definitions for Finance', source: 'ENGINE' })],
    rejected: [{ concept: 'Finance.NetRevenue', value: 'total', reason: 'Too generic to mean one concept' }],
  })
  const screen = await mount(<ChecksTab />)
  await screen.getByRole('button', { name: 'Suggest names' }).click()
  await screen.getByRole('dialog').getByRole('button', { name: 'Suggest names' }).click()
  expect(enrich).toHaveBeenCalledWith(expect.objectContaining({ domain_id: null }))
  await expect.element(screen.getByText('1 proposal sent for review')).toBeVisible()
  await expect.element(screen.getByText('Too generic to mean one concept')).toBeVisible()
  await expect.element(screen.getByText('total', { exact: true })).toBeVisible()
})

function metrics(overrides: Partial<OntologyMetrics['measured']> = {}): OntologyMetrics {
  return {
    measured: {
      answers: null,
      baseline: null,
      effect: null,
      claims: null,
      gold: null,
      joins: null,
      history: [],
      ...overrides,
    },
    live: {
      metric_coverage: { value: 0.576, successes: 34, n: 59, interval_95: [0.45, 0.69] },
      question_grounding: null,
      domains: [{ domain: 'Penjualan', concepts: 18, metrics: 14, bound: 14 }],
      stale_bindings: 3,
      checks: { warning: 2 },
      engine_proposals: { APPLIED: 9, REJECTED: 3 },
      engine_precision: { value: 0.75, successes: 9, n: 12, interval_95: [0.47, 0.91] },
    },
    not_measured: ['Answer accuracy needs a completed run with the ontology'],
  }
}

it('says what has not been measured instead of showing a number', async () => {
  vi.spyOn(ontologyApi, 'metrics').mockResolvedValue(metrics())
  const screen = await mount(<OverviewTab />)
  await expect.element(screen.getByRole('heading', { name: 'Answer accuracy' })).toBeVisible()
  await expect.element(screen.getByText('Run the gold set with the ontology to measure it.')).toBeVisible()
  await expect.element(screen.getByText('75% of 12')).toBeVisible()
  await expect.element(screen.getByText('14/14 metrics bound', { exact: false })).toBeVisible()
})

it('shows measured accuracy with its interval, n and the ontology effect', async () => {
  const measured = (value: number, n: number) => ({
    value,
    successes: Math.round(value * n),
    n,
    interval_95: [value - 0.05, Math.min(1, value + 0.04)] as [number, number],
  })
  vi.spyOn(ontologyApi, 'metrics').mockResolvedValue(
    metrics({
      answers: {
        run_id: 'r1',
        at: '2026-11-08T00:00:00Z',
        release_id: 'ont_rel_0005',
        set: 'serbadata v2',
        answer_accuracy: measured(0.86, 109),
        reliability: { value: 0.71, n: 150 },
        wrong_answers: measured(0.05, 150),
        abstention: measured(0.9, 41),
        clarification: measured(0.8, 15),
        failed: 2,
        outcomes: { 'answerable:correct': 94, 'answerable:wrong': 15, 'unanswerable:abstained': 26 },
        latency_ms_p50: 4000,
      },
      effect: {
        n: 109,
        without: measured(0.62, 109),
        with: measured(0.86, 109),
        difference: 0.24,
        mcnemar: { gained: 30, lost: 4, p_value: 0.00001 },
      },
    })
  )
  const screen = await mount(<OverviewTab />)
  await expect.element(screen.getByText('86%', { exact: true })).toBeVisible()
  await expect.element(screen.getByText('+24 pts')).toBeVisible()
  await expect.element(screen.getByText(/n=109 questions/)).toBeVisible()
  await expect.element(screen.getByText('30 questions fixed, 4 broken by the ontology')).toBeVisible()
  await expect.element(screen.getByText('Wrong answer')).toBeVisible()
})

it('shows the joins found in the data, what Nova decided and how honest its probability is', async () => {
  const measured = (successes: number, n: number) => ({
    value: n ? successes / n : null,
    successes,
    n,
    interval_95: [Math.max(0, successes / n - 0.08), Math.min(1, successes / n + 0.02)] as [number, number],
  })
  vi.spyOn(ontologyApi, 'metrics').mockResolvedValue(
    metrics({
      joins: {
        run_id: 'evn_j',
        at: '2026-11-08T00:00:00Z',
        databases: ['NOVA_RETAIL_FIELD'],
        profiled_columns: 227,
        profile_seconds: 6.4,
        discover_seconds: 37.6,
        candidates: 583,
        joins: { precision: measured(34, 41), recall: measured(34, 34), f1: 0.91, predicted: 41, gold: 34 },
        joins_reviewed: { precision: measured(41, 41), recall: measured(41, 41), f1: 1, predicted: 41, gold: 41 },
        undeclared_reviewed: 7,
        drift: {
          compared_with: 'evn_prev',
          findings: [{ column: 'LAB.orders.o_custkey', rule: 'null_rate_rose', before: 0, after: 0.2 }],
        },
        history: { families: 1789, pairs: 6 },
        calibration: {
          ece: 0.0325,
          brier: 0.0168,
          auc: 0.998,
          n: 583,
          positives: 41,
          bins: [
            { low: 0, high: 0.1, n: 540, confidence: 0.002, accuracy: 0 },
            { low: 0.9, high: 1, n: 43, confidence: 0.96, accuracy: 1 },
          ],
        },
        decisions: {
          labelled: 583,
          automatic: measured(519, 583),
          accepted_precision: measured(13, 13),
          rejected_misses: measured(0, 506),
          review_queue: 39,
        },
      },
    })
  )
  const screen = await mount(<OverviewTab />)
  await expect.element(screen.getByRole('heading', { name: 'Joins found in the data' })).toBeVisible()
  await expect.element(screen.getByText('227 columns profiled in 44 s')).toBeVisible()
  await expect.element(screen.getByText(/F1 1.00 against declared and reviewer-confirmed joins/)).toBeVisible()
  await expect.element(screen.getByText('0 of 506 rejected pairs were real joins · 39 left for review')).toBeVisible()
  await expect.element(screen.getByText('ECE 0.033 · AUC 0.998 · n=583')).toBeVisible()
  await expect.element(screen.getByText('1 change since the last profile')).toBeVisible()
})

it('keeps evaluation runs to ACCOUNTADMIN and lists them for an administrator', async () => {
  signIn('ana', 'finance_analyst')
  const runs = vi.spyOn(ontologyApi, 'evaluationRuns').mockResolvedValue([])
  const first = await mount(<EvaluationTab />)
  await expect.element(first.getByText('Evaluation runs are for ACCOUNTADMIN')).toBeVisible()
  expect(runs).not.toHaveBeenCalled()
  await cleanup()

  signIn('nova_admin', 'ACCOUNTADMIN')
  runs.mockResolvedValue([
    {
      id: 'evn_1',
      kind: 'answers',
      set_name: 'serbadata',
      version: 2,
      mode: 'with_ontology',
      release_id: 'ont_rel_0005',
      status: 'COMPLETED',
      stage: 'done',
      done: 150,
      total: 150,
      summary: { answer_accuracy: { value: 0.86, n: 109 } },
      error: null,
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:10:00Z',
      actor: { principal: 'nova_admin' },
    },
  ])
  const second = await mount(<EvaluationTab />)
  await expect.element(second.getByText('86% correct of 109')).toBeVisible()
  await expect.element(second.getByRole('button', { name: 'Answers · with ontology' })).toBeVisible()
})

it('lists the joins found in the data with probability, decision and evidence', async () => {
  signIn('nova_admin', 'ACCOUNTADMIN')
  vi.spyOn(ontologyApi, 'evaluationRuns').mockResolvedValue([
    {
      id: 'evn_j',
      kind: 'joins',
      set_name: 'joins',
      version: 1,
      mode: null,
      release_id: null,
      status: 'COMPLETED',
      stage: 'done',
      done: 33,
      total: 33,
      summary: { joins: { f1: 0.91, predicted: 41 }, joins_reviewed: { f1: 1, predicted: 41 } },
      error: null,
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:10:00Z',
      actor: { principal: 'nova_admin' },
    },
  ])
  const join = (source: string, target: string, outcome: string, decision: string, probability: number) => ({
    id: `evr_${source}`,
    run_id: 'evn_j',
    item: `join:${source}->${target}`,
    outcome,
    detail: {
      source,
      target,
      containment: 1,
      probability,
      decision,
      declared: outcome === 'proposed',
      explanation: { evidence: 'chosen by the rule, column names the target table, same id-like name', right: 38, labelled: 38 },
    },
    reviews: [],
  })
  vi.spyOn(ontologyApi, 'evaluationResults').mockResolvedValue([
    join('LAB.sales.outlet_id', 'LAB.outlets.outlet_id', 'proposed', 'accept', 0.987),
    join('LAB.sales.quantity', 'LAB.products.product_id', 'rejected', 'reject', 0.001),
  ])
  const screen = await mount(<EvaluationTab />)
  await expect.element(screen.getByText('41 joins · F1 1.00')).toBeVisible()
  await screen.getByRole('button', { name: /Join discovery/ }).click()
  await expect.element(screen.getByText('sales.outlet_id')).toBeVisible()
  await expect.element(screen.getByText('99%')).toBeVisible()
  await expect.element(screen.getByText(/38 of 38 like it were right/)).toBeVisible()
  // Rejected pairs stay out of the default list of proposed joins.
  await expect.element(screen.getByText('sales.quantity')).not.toBeInTheDocument()
})

it('turns measured failures into steward proposals on request', async () => {
  signIn('nova_admin', 'ACCOUNTADMIN')
  vi.spyOn(ontologyApi, 'evaluationRuns').mockResolvedValue([])
  const learn = vi.spyOn(ontologyApi, 'learn').mockResolvedValue({
    proposals: [{ id: 'prp_1', title: 'Remove names the claim audit found misleading', changes: 2 }],
    skipped: [],
  })
  const { toast } = await import('sonner')
  const success = vi.spyOn(toast, 'success')
  const screen = await mount(<EvaluationTab />)
  await screen.getByRole('button', { name: 'Learn from failures' }).click()
  await vi.waitFor(() => expect(success).toHaveBeenCalledWith('1 proposal sent to the stewards'))
  expect(learn).toHaveBeenCalledOnce()
})

