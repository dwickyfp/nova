import { createFileRoute, redirect } from '@tanstack/react-router'

// The External Catalogs form moved: catalogs are created in Database Explorer,
// and the sources they come from are managed on the Connections page.
export const Route = createFileRoute('/_authenticated/external-catalogs/')({
  beforeLoad: () => {
    throw redirect({ to: '/connections' })
  },
})
