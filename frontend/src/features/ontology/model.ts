import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { StatusTone } from '@/components/ui/status-badge'
import { useAuthStore } from '@/stores/auth-store'
import {
  ontologyApi,
  type Change,
  type Concept,
  type ConceptKind,
  type Domain,
  type ProposalStatus,
  type RelationType,
  type ReleaseSummary,
} from './api'

export const KIND_LABELS: Record<ConceptKind, string> = {
  METRIC: 'Metric',
  MEASURE: 'Measure',
  DIMENSION: 'Dimension',
  BUSINESS_ENTITY: 'Business entity',
  TERM: 'Term',
  POLICY: 'Policy',
  GOAL: 'Goal',
  BUSINESS_EVENT_TYPE: 'Event type',
  ACTION_TYPE: 'Action type',
  PROCESS: 'Process',
}

export const RELATION_LABELS: Record<RelationType, string> = {
  IS_A: 'is a kind of',
  HAS_PART: 'has part',
  DERIVES: 'derives',
  MEASURES: 'measures',
  APPLIES_TO: 'applies to',
  EQUIVALENT_TO: 'is equivalent to',
  RELATED_TO: 'is related to',
  AFFECTS_GOAL: 'affects goal',
}

export const PROPOSAL_TONE: Record<ProposalStatus, StatusTone> = {
  PENDING: 'warning',
  APPROVED: 'info',
  APPLIED: 'success',
  REJECTED: 'danger',
  WITHDRAWN: 'neutral',
}

export const RELEASE_TONE: Record<ReleaseSummary['status'], StatusTone> = {
  PREPARED: 'info',
  BLOCKED: 'danger',
  PUBLISHED: 'success',
}

export function useIsAdmin(): boolean {
  return useAuthStore(
    (state) => (state.auth.user?.activeRole ?? '').toUpperCase() === 'ACCOUNTADMIN'
  )
}

/** Query keys carry the security epoch, so a role switch never shows cached meaning. */
export function useOntologyKey(...parts: unknown[]): unknown[] {
  const epoch = useAuthStore((state) => state.securityEpoch)
  return ['ontology', epoch, ...parts]
}

export function useDomains() {
  return useQuery({ queryKey: useOntologyKey('domains'), queryFn: ontologyApi.domains })
}

export function useWorkingConcepts() {
  return useQuery({
    queryKey: useOntologyKey('concepts', 'working', '', ''),
    queryFn: () => ontologyApi.concepts({ scope: 'working' }),
  })
}

export function splitList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/**
 * Names for every concept a proposal mentions: existing ones from the working
 * state, and the ones the proposal itself creates.
 */
export function conceptNames(
  changes: Change[],
  concepts: Concept[],
  domains: Map<string, Domain>
): Map<string, string> {
  const names = new Map(concepts.map((item) => [item.id, item.qualified_name]))
  for (const change of changes) {
    if (change.target === 'concept' && change.op === 'create' && change.body && change.concept_id) {
      const domain = domains.get(change.body.domain_id)?.qualified_path ?? 'New'
      names.set(change.concept_id, `${domain}.${change.body.slug}`)
    }
  }
  return names
}

/** One readable line per proposed change. */
export function describeChange(change: Change, names: Map<string, string>): string {
  const name = (conceptId: string | undefined) =>
    (conceptId && names.get(conceptId)) || 'a concept you cannot see'
  if (change.target === 'concept') {
    if (change.op === 'create' && change.body) {
      return `Add ${KIND_LABELS[change.body.kind].toLowerCase()} ${name(change.concept_id)}`
    }
    if (change.op === 'update') return `Revise ${name(change.concept_id)}`
    return `Deprecate ${name(change.concept_id)}`
  }
  if (change.target === 'relation') {
    const line = `${name(change.source_id)} ${RELATION_LABELS[change.type]} ${name(change.target_id)}`
    return change.op === 'create' ? `Link ${line}` : `Remove the link ${line}`
  }
  if (change.op === 'create') {
    return `Compute ${name(change.concept_id)} with ${change.view_id} metric ${change.metric}`
  }
  return `Revoke binding ${change.representation_id}`
}

/** Proposals that change a concept, for the concept detail. */
export function useConceptProposals(conceptId: string) {
  const proposals = useQuery({
    queryKey: useOntologyKey('proposals', 'PENDING'),
    queryFn: () => ontologyApi.proposals('PENDING'),
  })
  return useMemo(
    () =>
      (proposals.data ?? []).filter((proposal) =>
        proposal.changes.some(
          (change) =>
            (change.target === 'concept' && change.concept_id === conceptId) ||
            (change.target === 'relation' &&
              (change.source_id === conceptId || change.target_id === conceptId)) ||
            (change.target === 'representation' && change.concept_id === conceptId)
        )
      ),
    [proposals.data, conceptId]
  )
}
