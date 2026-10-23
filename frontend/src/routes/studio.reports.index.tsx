import { createFileRoute } from '@tanstack/react-router'
import { ReportsLibrary } from '@/features/smart-reports/library'

export const Route = createFileRoute('/studio/reports/')({ component: ReportsLibrary })
