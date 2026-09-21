import { createFileRoute } from '@tanstack/react-router'
import { SkillsRegistryPage } from '@/features/agents/skills-registry-page'

export const Route = createFileRoute('/_authenticated/agents/skills')({
  component: SkillsRegistryPage,
})
