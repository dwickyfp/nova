import { useQuery } from '@tanstack/react-query'
import { metadataApi } from '@/features/agents/metadata-api'

export function useDatasetDatabases(securityEpoch: number) {
  return useQuery({
    queryKey: ['native-dataviz', 'source-databases', securityEpoch],
    queryFn: ({ signal }) => metadataApi.listDatabases(false, signal),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
}
