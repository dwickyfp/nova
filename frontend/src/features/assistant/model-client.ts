import { api } from '@/lib/api-client'

/**
 * Provider/model options for the assistant's model selector. Read-only views of
 * the same records the AI Providers page manages; the endpoints are readable by
 * any authenticated user, so the panel does not need admin rights to populate
 * the selector. The API key never appears here — the list response masks it.
 */

export type AssistantProviderRecord = {
  id: string
  name: string
  type: string
  endpoint: string
  is_active: boolean
  has_api_key: boolean
}

export type AssistantModel = {
  id: string
  provider_id: string
  name: string
  display_name: string | null
  type: string
  is_active: boolean
}

export type ModelOption = {
  /** Model name sent to the backend for a turn. */
  model: string
  providerId: string
  providerName: string
  label: string
}

export async function listModelOptions(): Promise<ModelOption[]> {
  const { providers } = await api.get<{ providers: AssistantProviderRecord[] }>('/ai/providers')
  const usable = providers.filter(
    (provider) => provider.is_active && provider.has_api_key
  )

  const options = await Promise.all(
    usable.map(async (provider) => {
      const { models } = await api.get<{ models: AssistantModel[] }>(
        `/ai/providers/${encodeURIComponent(provider.id)}/models`
      )
      return models
        .filter((model) => model.is_active && model.type === 'llm')
        .map<ModelOption>((model) => ({
          model: model.name,
          providerId: provider.id,
          providerName: provider.name,
          label: model.display_name || model.name,
        }))
    })
  )

  return options.flat()
}
