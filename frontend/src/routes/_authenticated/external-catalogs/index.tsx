import { createFileRoute } from '@tanstack/react-router'
import { ExternalCatalogsPage } from '@/features/external-catalogs'

export const Route = createFileRoute('/_authenticated/external-catalogs/')({
  component: ExternalCatalogsPage,
})
