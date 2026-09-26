import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'

type DecisionSettings = {
  enabled: boolean
  decision_model_id: string | null
  light_model_id: string | null
  heavy_model_id: string | null
  min_probability: number
  min_confidence: number
  timeout_seconds: number
}

type Model = {
  id: string
  name: string
  display_name?: string
  type: string
  is_active: boolean
}
type Provider = { id: string; name: string; type: string; is_active: boolean }
type Option = { id: string; label: string; kind: 'decision' | 'llm' }

export function DecisionTab() {
  const [settings, setSettings] = useState<DecisionSettings | null>(null)
  const [saved, setSaved] = useState<DecisionSettings | null>(null)
  const [options, setOptions] = useState<Option[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [config, { providers }] = await Promise.all([
        api.get<DecisionSettings>('/ai/decision-settings'),
        api.get<{ providers: Provider[] }>('/ai/providers'),
      ])
      const groups = await Promise.all(
        providers
          .filter((p) => p.is_active)
          .map(async (provider) => {
            const { models } = await api.get<{ models: Model[] }>(
              `/ai/providers/${provider.id}/models`,
            )
            return models
              .filter(
                (m) =>
                  m.is_active &&
                  (provider.type === 'decision'
                    ? m.type === 'decision'
                    : m.type === 'llm'),
              )
              .map((m) => ({
                id: m.id,
                label: `${provider.name} / ${m.display_name || m.name}`,
                kind: m.type as Option['kind'],
              }))
          }),
      )
      setOptions(groups.flat())
      setSettings(config)
      setSaved(config)
    } catch {
      setError('Could not load decision settings. Try again.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (loading)
    return (
      <p role="status" className="py-6 text-sm text-muted-foreground">
        Loading decision settings…
      </p>
    )
  if (!settings)
    return (
      <div className="space-y-3 py-6">
        <p role="alert" className="text-sm">
          {error}
        </p>
        <Button variant="outline" onClick={() => void load()}>
          Try again
        </Button>
      </div>
    )

  const fields = [
    {
      key: 'decision_model_id',
      label: 'Decision model',
      kind: 'decision',
      help: 'Ranks workloads, agents, skills, tools, Semantic Views, and supported ML choices.',
    },
    {
      key: 'light_model_id',
      label: 'Light workload model',
      kind: 'llm',
      help: 'Simple questions, rewrites, and straightforward lookups.',
    },
    {
      key: 'heavy_model_id',
      label: 'Heavy workload model',
      kind: 'llm',
      help: 'Audits, diagnosis, complex SQL, ML, and analysis across specialists.',
    },
  ] as const
  const missing = fields.some(
    ({ key, kind }) =>
      !options.some(
        (option) => option.id === settings[key] && option.kind === kind,
      ),
  )
  const dirty = JSON.stringify(settings) !== JSON.stringify(saved)

  async function save() {
    setSaving(true)
    setError('')
    try {
      const result = await api.put<DecisionSettings>(
        '/ai/decision-settings',
        settings,
      )
      setSettings(result)
      setSaved(result)
      toast.success('Decision settings saved')
    } catch {
      setError(
        'Could not save. Check that the models are active and you have an admin role.',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      className="relative max-w-2xl space-y-6 pb-6"
      onSubmit={(event) => {
        event.preventDefault()
        void save()
      }}
    >
      <div className="flex items-start justify-between gap-4 border-b pb-5">
        <div className="space-y-1">
          <Label htmlFor="decision-enabled" className="text-base">
            Decision mode
          </Label>
          <p id="decision-mode-help" className="text-sm text-muted-foreground">
            Use a decision model to guide Nova Studio. Changes apply to new
            turns after saving.
          </p>
        </div>
        <Switch
          className="relative mx-1.5 my-3.5 before:absolute before:-inset-x-1.5 before:-inset-y-3.5"
          id="decision-enabled"
          checked={settings.enabled}
          disabled={saving}
          aria-describedby="decision-mode-help"
          onCheckedChange={(enabled) => setSettings({ ...settings, enabled })}
        />
      </div>
      <p className="text-sm text-muted-foreground">
        When enabled, workload routing selects the answer model for Studio
        turns. When disabled, Studio uses its existing flow and model selection.
        Uncertain or unavailable decisions fall back to the existing flow.
      </p>
      <div className="space-y-5">
        {fields.map(({ key, label, kind, help }) => {
          const candidates = options.filter((option) => option.kind === kind)
          const value = settings[key]
          const unavailable =
            value && !candidates.some((option) => option.id === value)
          return (
            <div key={key} className="min-w-0 space-y-2">
              <Label htmlFor={key}>{label}</Label>
              <Select
                value={value ?? ''}
                disabled={saving || !candidates.length}
                onValueChange={(id) => setSettings({ ...settings, [key]: id })}
              >
                <SelectTrigger
                  id={key}
                  className="w-full min-w-0"
                  aria-describedby={`${key}-help`}
                >
                  <SelectValue
                    placeholder={
                      candidates.length
                        ? 'Select a model'
                        : `No active ${kind === 'decision' ? 'decision' : 'LLM'} models`
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {unavailable && (
                    <SelectItem value={value} disabled>
                      Selected model is unavailable
                    </SelectItem>
                  )}
                  {candidates.map((option) => (
                    <SelectItem key={option.id} value={option.id}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p id={`${key}-help`} className="text-sm text-muted-foreground">
                {help}
              </p>
            </div>
          )
        })}
      </div>
      {settings.enabled && missing && (
        <p role="status" className="text-sm">
          Select three active models before enabling. Register missing models in
          the Providers tab.
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="submit"
          className="hover:bg-primary"
          disabled={saving || !dirty || (settings.enabled && missing)}
        >
          {saving ? 'Saving…' : 'Save settings'}
        </Button>
        {dirty && (
          <span className="text-sm text-muted-foreground" role="status">
            Unsaved changes
          </span>
        )}
      </div>
    </form>
  )
}
