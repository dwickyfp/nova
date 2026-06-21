import { useCallback, useEffect, useMemo, useState } from 'react'
import { Loader2, Play, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api-client'

type Alias = {
  alias_name: string
  model_id: string
  model_name: string | null
  version: number
}

type ModelInfo = {
  model_id: string
  model_name: string
  feature_columns: string[] | null
  model_type: string
}

type PredictionResult = {
  model_alias: string
  model_name: string
  prediction: unknown
  probability: Record<string, number> | null
  model_version: number
} | null

export function PredictTab() {
  const [aliases, setAliases] = useState<Alias[]>([])
  const [models, setModels] = useState<ModelInfo[]>([])
  const [loading, setLoading] = useState(true)

  // Single predict
  const [selectedAlias, setSelectedAlias] = useState('')
  const [featureValues, setFeatureValues] = useState<Record<string, string>>({})
  const [predicting, setPredicting] = useState(false)
  const [result, setResult] = useState<PredictionResult>(null)

  // Batch predict
  const [batchAlias, setBatchAlias] = useState('')
  const [batchSql, setBatchSql] = useState('')
  const [batchDb, setBatchDb] = useState('')
  const [batchPredicting, setBatchPredicting] = useState(false)
  const [batchResult, setBatchResult] = useState<
    { predictions: unknown[]; total_rows: number } | null
  >(null)

  // Result dialog
  const [resultOpen, setResultOpen] = useState(false)

  const fetchAliases = useCallback(async () => {
    try {
      const res = await api.get<{ aliases: Alias[]; count: number }>(
        '/ml/aliases'
      )
      setAliases(res.aliases)
      if (res.aliases.length > 0 && !selectedAlias) {
        setSelectedAlias(res.aliases[0].alias_name)
      }
    } catch {
      // silent
    }
  }, [selectedAlias])

  const fetchModels = useCallback(async () => {
    try {
      const res = await api.get<{ models: ModelInfo[]; count: number }>(
        '/ml/models'
      )
      setModels(res.models)
    } catch {
      // silent
    }
  }, [])

  useEffect(() => {
    Promise.all([fetchAliases(), fetchModels()]).finally(() =>
      setLoading(false)
    )
  }, [fetchAliases, fetchModels])

  // Get feature columns for selected alias
  const selectedModel = useMemo(() => {
    const alias = aliases.find((a) => a.alias_name === selectedAlias)
    if (!alias) return null
    return models.find((m) => m.model_id === alias.model_id) ?? null
  }, [selectedAlias, aliases, models])

  const featureColumns = selectedModel?.feature_columns ?? []

  const handlePredict = async () => {
    if (!selectedAlias) {
      toast.error('Please select a model alias')
      return
    }
    setPredicting(true)
    try {
      const features: Record<string, number> = {}
      for (const col of featureColumns) {
        const val = featureValues[col]
        if (val === undefined || val === '') {
          toast.error(`Missing value for feature: ${col}`)
          setPredicting(false)
          return
        }
        features[col] = parseFloat(val)
      }
      const res = await api.post<PredictionResult>('/ml/predict', {
        model_alias: selectedAlias,
        features,
      })
      setResult(res)
      setResultOpen(true)
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Prediction failed'
      )
    } finally {
      setPredicting(false)
    }
  }

  const handleBatchPredict = async () => {
    if (!batchAlias) {
      toast.error('Please select a model alias')
      return
    }
    if (!batchSql.trim()) {
      toast.error('Please enter a prediction SQL query')
      return
    }
    setBatchPredicting(true)
    try {
      const res = await api.post<{
        predictions: unknown[]
        total_rows: number
      }>('/ml/predict/batch', {
        model_alias: batchAlias,
        prediction_sql: batchSql,
        database_name: batchDb || null,
      })
      setBatchResult(res)
      toast.success(`Batch prediction complete: ${res.total_rows} rows`)
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Batch prediction failed'
      )
    } finally {
      setBatchPredicting(false)
    }
  }

  if (loading) {
    return (
      <div className='flex items-center justify-center py-12'>
        <Loader2 className='size-5 animate-spin text-muted-foreground' />
      </div>
    )
  }

  if (aliases.length === 0) {
    return (
      <div className='rounded-lg bg-card p-8 text-center'>
        <Sparkles className='mx-auto mb-3 size-8 text-muted-foreground' />
        <p className='text-sm text-muted-foreground'>
          No model aliases found. Train a model and create an alias first.
        </p>
      </div>
    )
  }

  return (
    <div className='space-y-6'>
      {/* Single Prediction */}
      <div className='rounded-lg bg-card p-6'>
        <h3 className='mb-4 text-sm font-semibold'>Single Prediction</h3>
        <div className='space-y-4'>
          <div>
            <Label className='mb-1.5 block text-xs'>Model Alias</Label>
            <Select value={selectedAlias} onValueChange={setSelectedAlias}>
              <SelectTrigger>
                <SelectValue placeholder='Select alias' />
              </SelectTrigger>
              <SelectContent>
                {aliases.map((a) => (
                  <SelectItem key={a.alias_name} value={a.alias_name}>
                    {a.alias_name} ({a.model_name})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {featureColumns.length > 0 && (
            <div className='grid grid-cols-2 gap-3'>
              {featureColumns.map((col) => (
                <div key={col}>
                  <Label className='mb-1.5 block text-xs'>{col}</Label>
                  <Input
                    type='number'
                    step='any'
                    placeholder={`Enter ${col}`}
                    value={featureValues[col] ?? ''}
                    onChange={(e) =>
                      setFeatureValues((prev) => ({
                        ...prev,
                        [col]: e.target.value,
                      }))
                    }
                  />
                </div>
              ))}
            </div>
          )}

          <Button onClick={handlePredict} disabled={predicting}>
            {predicting ? (
              <Loader2 className='mr-2 size-3.5 animate-spin' />
            ) : (
              <Play className='mr-2 size-3.5' />
            )}
            Run Prediction
          </Button>
        </div>
      </div>

      {/* Batch Prediction */}
      <div className='rounded-lg bg-card p-6'>
        <h3 className='mb-4 text-sm font-semibold'>Batch Prediction</h3>
        <div className='space-y-4'>
          <div>
            <Label className='mb-1.5 block text-xs'>Model Alias</Label>
            <Select value={batchAlias} onValueChange={setBatchAlias}>
              <SelectTrigger>
                <SelectValue placeholder='Select alias' />
              </SelectTrigger>
              <SelectContent>
                {aliases.map((a) => (
                  <SelectItem key={a.alias_name} value={a.alias_name}>
                    {a.alias_name} ({a.model_name})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className='mb-1.5 block text-xs'>
              Prediction SQL (return feature columns only)
            </Label>
            <Textarea
              rows={4}
              placeholder='SELECT total_amount FROM NOVA_EXAMPLE.orders LIMIT 10'
              value={batchSql}
              onChange={(e) => setBatchSql(e.target.value)}
              className='font-mono text-xs'
            />
          </div>
          <div>
            <Label className='mb-1.5 block text-xs'>
              Database (optional)
            </Label>
            <Input
              placeholder='NOVA_EXAMPLE'
              value={batchDb}
              onChange={(e) => setBatchDb(e.target.value)}
            />
          </div>
          <Button onClick={handleBatchPredict} disabled={batchPredicting}>
            {batchPredicting ? (
              <Loader2 className='mr-2 size-3.5 animate-spin' />
            ) : (
              <Play className='mr-2 size-3.5' />
            )}
            Run Batch Prediction
          </Button>

          {batchResult && (
            <div className='rounded-md bg-muted/50 p-3'>
              <p className='mb-2 text-xs font-medium'>
                Results: {batchResult.total_rows} predictions
              </p>
              <pre className='max-h-48 overflow-auto text-xs'>
                {JSON.stringify(batchResult.predictions, null, 2)}
              </pre>
            </div>
          )}
        </div>
      </div>

      {/* Result Dialog */}
      <Dialog open={resultOpen} onOpenChange={setResultOpen}>
        <DialogContent className='max-w-md'>
          <DialogHeader>
            <DialogTitle>Prediction Result</DialogTitle>
            <DialogDescription>
              Model: {result?.model_name} (v{result?.model_version})
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-3'>
            <div>
              <Label className='text-xs'>Prediction</Label>
              <div className='mt-1'>
                <Badge variant='default' className='text-sm'>
                  {String(result?.prediction)}
                </Badge>
              </div>
            </div>
            {result?.probability && (
              <div>
                <Label className='text-xs'>Probability</Label>
                <div className='mt-1 space-y-1'>
                  {Object.entries(result.probability).map(([k, v]) => (
                    <div
                      key={k}
                      className='flex items-center justify-between text-xs'
                    >
                      <span className='text-muted-foreground'>{k}</span>
                      <span className='font-mono'>
                        {(v * 100).toFixed(1)}%
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
