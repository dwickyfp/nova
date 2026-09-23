import { useQuery } from '@tanstack/react-query'
import { metadataApi } from '@/features/agents/metadata-api'

export function useRelationColumns(relation: string) {
  const [database, name] = relation.split('.')
  return useQuery({
    queryKey: ['intelligence', 'columns', relation],
    queryFn: () => metadataApi.getTableDetail(database, name),
    enabled: Boolean(database && name),
  })
}
