import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Brain,
  CheckCircle2,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  XCircle,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  SimpleTableToolbar,
  SimpleTableViewport,
} from '@/components/data-table/simple-table-controls'
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'

// ── Types ──────────────────────────────────────────────────────

type ModelType = 'classification' | 'regression'

type Algorithm =
  | 'auto'
  | 'linear'
  | 'logistic'
  | 'decision_tree'
  | 'random_forest'
  | 'gradient_boost'
  | 'knn'
  | 'svm'

type ModelInfo = {
  model_id: string
  model_name: string
  model_type: string
  algorithm: string | null
  target_column: string | null
  feature_columns: string[] | null
  hyperparameters: Record<string, unknown> | null
  training_sql: string | null
  database_name: string | null
  created_at: string | null
  created_by: string | null
  latest_version: number | null
  latest_status: string | null
  latest_metrics: Record<string, unknown> | null
  training_rows: number | null
}

type TrainResponse = {
  model_id: string
  model_name: string
  model_type: string
  algorithm: string
  version: number
  status: string
  training_rows: number
  feature_columns: string[]
  metrics: Record<string, unknown>
  message: string | null
}

// ── Constants ───────────────────────────────────────────────────

const MODEL_TYPES: { value: ModelType; label: string }[] = [
  { value: 'classification', label: 'Classification' },
  { value: 'regression', label: 'Regression' },
]

const ALGORITHMS: { value: Algorithm; label: string; description: string }[] = [
  { value: 'auto', label: 'Auto', description: 'Best algorithm chosen automatically' },
  { value: 'linear', label: 'Linear', description: 'Linear / Ridge regression' },
  { value: 'logistic', label: 'Logistic', description: 'Logistic regression' },
  { value: 'decision_tree', label: 'Decision Tree', description: 'Single decision tree' },
  { value: 'random_forest', label: 'Random Forest', description: 'Ensemble of decision trees' },
  { value: 'gradient_boost', label: 'Gradient Boost', description: 'Gradient boosted trees (XGBoost-style)' },
  { value: 'knn', label: 'KNN', description: 'K-nearest neighbors' },
  { value: 'svm', label: 'SVM', description: 'Support vector machine' },
]

const MODEL_TYPE_COLORS: Record<string, string> = {
  classification:
    'bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20',
  regression:
    'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20',
}

const ALGORITHM_COLORS: Record<string, string> = {
  linear: 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border-cyan-500/20',
  logistic: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20',
  decision_tree: 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20',
  random_forest: 'bg-green-500/10 text-green-600 dark:text-green-400 border-green-500/20',
  gradient_boost: 'bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20',
  knn: 'bg-pink-500/10 text-pink-600 dark:text-pink-400 border-pink-500/20',
  svm: 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20',
  auto: 'bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-500/20',
}

const emptyTrainForm = {
  model_name: '',
  model_type: 'classification' as ModelType,
  algorithm: 'auto' as Algorithm,
  training_sql: '',
  target_column: '',
  feature_columns: '',
  test_size: '0.2',
  hyperparameters: '',
  database_name: '',
}

// ── Helpers ─────────────────────────────────────────────────────

function getAlgorithmLabel(algorithm: string): string {
  return (
    ALGORITHMS.find((a) => a.value === algorithm)?.label ??
    algorithm
  )
}

function formatMetricValue(key: string, value: unknown): string {
  if (typeof value === 'number') {
    if (key.includes('score') || key.includes('accuracy') || key.includes('precision') || key.includes('recall') || key.includes('f1')) {
      return `${(value * 100).toFixed(1)}%`
    }
    if (key.includes('error') || key.includes('loss')) {
      return value.toFixed(4)
    }
    return value.toFixed(3)
  }
  return String(value)
}

function getPrimaryMetric(metrics: Record<string, unknown> | null): { key: string; value: string } | null {
  if (!metrics) return null
  // Prefer accuracy for classification, r2 for regression
  const priority = ['accuracy', 'r2', 'f1_macro', 'f1_weighted', 'rmse', 'mae', 'mse']
  for (const key of priority) {
    if (key in metrics) {
      return { key, value: formatMetricValue(key, metrics[key]) }
    }
  }
  // Fallback to first metric
  const firstKey = Object.keys(metrics)[0]
  if (firstKey) {
    return { key: firstKey, value: formatMetricValue(firstKey, metrics[firstKey]) }
  }
  return null
}

// ── Component ───────────────────────────────────────────────────

export function ModelsTab() {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [algoFilter, setAlgoFilter] = useState('')

  // Dialog state
  const [trainDialogOpen, setTrainDialogOpen] = useState(false)
  const [deleteDialog, setDeleteDialog] = useState<ModelInfo | null>(null)
  const [detailDialog, setDetailDialog] = useState<ModelInfo | null>(null)

  // Form state
  const [trainForm, setTrainForm] = useState({ ...emptyTrainForm })
  const [submitting, setSubmitting] = useState(false)

  // ── Data fetching ─────────────────────────────────────────────

  const fetchModels = useCallback(async () => {
    try {
      const res = await api.get<{ models: ModelInfo[]; count: number }>(
        '/ml/models'
      )
      setModels(res.models)
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Failed to load ML models'
      )
    }
  }, [])

  const fetchAll = useCallback(async () => {
    setLoading(true)
    await fetchModels()
    setLoading(false)
  }, [fetchModels])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  // ── Filtering ─────────────────────────────────────────────────

  const filtered = useMemo(() => {
    let result = models
    if (typeFilter) {
      result = result.filter((m) => m.model_type === typeFilter)
    }
    if (algoFilter) {
      const algoValue = ALGORITHMS.find((a) => a.label === algoFilter)?.value
      if (algoValue) {
        result = result.filter((m) => m.algorithm === algoValue)
      }
    }
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter(
        (m) =>
          m.model_name.toLowerCase().includes(q) ||
          m.model_type.toLowerCase().includes(q) ||
          (m.algorithm ?? '').toLowerCase().includes(q) ||
          (m.target_column ?? '').toLowerCase().includes(q) ||
          (m.created_by ?? '').toLowerCase().includes(q)
      )
    }
    return result
  }, [models, search, typeFilter, algoFilter])

  // ── Summary stats ─────────────────────────────────────────────

  const classificationCount = models.filter((m) => m.model_type === 'classification').length
  const regressionCount = models.filter((m) => m.model_type === 'regression').length
  const readyCount = models.filter((m) => m.latest_status === 'ready').length

  // ── Train Model Dialog ────────────────────────────────────────

  const openTrainDialog = () => {
    setTrainForm({ ...emptyTrainForm })
    setTrainDialogOpen(true)
  }

  const handleTrainModel = async () => {
    if (!trainForm.model_name.trim()) {
      toast.error('Model name is required')
      return
    }
    if (!trainForm.training_sql.trim()) {
      toast.error('Training SQL query is required')
      return
    }
    if (!trainForm.target_column.trim()) {
      toast.error('Target column is required')
      return
    }

    setSubmitting(true)
    try {
      const body: Record<string, unknown> = {
        model_name: trainForm.model_name.trim(),
        model_type: trainForm.model_type,
        algorithm: trainForm.algorithm,
        training_sql: trainForm.training_sql.trim(),
        target_column: trainForm.target_column.trim(),
        test_size: parseFloat(trainForm.test_size) || 0.2,
      }

      if (trainForm.feature_columns.trim()) {
        body.feature_columns = trainForm.feature_columns
          .split(',')
          .map((c) => c.trim())
          .filter(Boolean)
      }

      if (trainForm.hyperparameters.trim()) {
        body.hyperparameters = JSON.parse(trainForm.hyperparameters)
      }

      if (trainForm.database_name.trim()) {
        body.database_name = trainForm.database_name.trim()
      }

      const result = await api.post<TrainResponse>('/ml/train', body)

      const metricSummary = getPrimaryMetric(result.metrics)
      toast.success(
        `Model '${result.model_name}' trained — ${result.training_rows} rows, ${result.feature_columns.length} features` +
          (metricSummary ? `, ${metricSummary.key}: ${metricSummary.value}` : '')
      )

      setTrainDialogOpen(false)
      await fetchModels()
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Failed to train model'
      )
    } finally {
      setSubmitting(false)
    }
  }

  // ── Delete ────────────────────────────────────────────────────

  const handleDelete = async () => {
    if (!deleteDialog) return
    setSubmitting(true)
    try {
      await api.delete(`/ml/models/${deleteDialog.model_id}`)
      toast.success(`Model '${deleteDialog.model_name}' deleted`)
      setDeleteDialog(null)
      await fetchModels()
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Failed to delete model'
      )
    } finally {
      setSubmitting(false)
    }
  }

  // ── Filter options ────────────────────────────────────────────

  const typeFilterOptions = useMemo(
    () => MODEL_TYPES.map((mt) => mt.label),
    []
  )
  const algoFilterOptions = useMemo(
    () => ALGORITHMS.filter((a) => a.value !== 'auto').map((a) => a.label),
    []
  )

  // ── Render ────────────────────────────────────────────────────

  return (
    <div className='space-y-4'>
      {/* Model Summary Bar */}
      <div className='flex items-center gap-3 rounded-lg border bg-card p-3'>
        <div className='flex items-center gap-2'>
          <Brain className='size-4 text-muted-foreground' />
          <span className='text-sm font-medium'>Trained Models</span>
        </div>
        <div className='flex items-center gap-2'>
          <Badge variant='secondary' className='gap-1 font-mono text-xs'>
            <CheckCircle2 className='size-3 text-emerald-500' />
            {readyCount}/{models.length} ready
          </Badge>
          <Badge variant='outline' className='text-[10px] font-normal' style={{}}>
            <span className={cn(MODEL_TYPE_COLORS.classification, 'rounded px-1')}>
              {classificationCount} classification
            </span>
          </Badge>
          <Badge variant='outline' className='text-[10px] font-normal'>
            <span className={cn(MODEL_TYPE_COLORS.regression, 'rounded px-1')}>
              {regressionCount} regression
            </span>
          </Badge>
        </div>
        <div className='ml-auto flex items-center gap-2'>
          <Button
            variant='outline'
            size='sm'
            className='h-7 gap-1.5 text-xs'
            onClick={fetchAll}
          >
            <Sparkles className='size-3' />
            Refresh
          </Button>
        </div>
      </div>

      {/* Filters */}
      <SimpleTableToolbar
        search={search}
        onSearchChange={(value) => {
          setSearch(value)
        }}
        searchPlaceholder='Search models, algorithms, columns...'
        resultLabel={`${filtered.length} model${filtered.length !== 1 ? 's' : ''}`}
        filters={[
          {
            label: 'Model Type',
            value:
              typeFilter &&
              MODEL_TYPES.find((mt) => mt.value === typeFilter)?.label
                ? MODEL_TYPES.find((mt) => mt.value === typeFilter)!.label
                : '',
            options: typeFilterOptions,
            onChange: (label) => {
              const mt = MODEL_TYPES.find((m) => m.label === label)
              setTypeFilter(mt?.value ?? '')
            },
            icon: <Brain size={14} />,
          },
          {
            label: 'Algorithm',
            value: algoFilter,
            options: algoFilterOptions,
            onChange: (label) => {
              setAlgoFilter(label)
            },
            icon: <Sparkles size={14} />,
          },
        ]}
      />

      {/* Toolbar action */}
      <div className='flex justify-end'>
        <Button size='sm' className='gap-1.5' onClick={openTrainDialog}>
          <Plus className='size-3.5' />
          Train New Model
        </Button>
      </div>

      {/* Table */}
      <SimpleTableViewport>
        <table className='w-full text-sm'>
          <thead>
            <tr>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Model Name
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Type
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Algorithm
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Target
              </th>
              <th className='px-4 py-3 text-center text-xs font-medium text-muted-foreground'>
                Status
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Metrics
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Rows
              </th>
              <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                Created
              </th>
              <th className='px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={9} className='px-4 py-12 text-center'>
                  <Loader2 className='mx-auto size-5 animate-spin text-muted-foreground' />
                </td>
              </tr>
            )}

            {!loading && filtered.length === 0 && (
              <tr>
                <td colSpan={9} className='px-4 py-12 text-center'>
                  <Brain className='mx-auto mb-3 size-8 text-muted-foreground' />
                  <p className='text-sm text-muted-foreground'>
                    No ML models trained yet.
                  </p>
                  <p className='mt-1 text-xs text-muted-foreground'>
                    Train classification or regression models using data from
                    SQL queries. Supports linear models, trees, ensembles, KNN,
                    and SVM.
                  </p>
                  <Button
                    variant='outline'
                    size='sm'
                    className='mt-3 gap-1.5'
                    onClick={openTrainDialog}
                  >
                    <Plus className='size-3.5' />
                    Train your first model
                  </Button>
                </td>
              </tr>
            )}

            {!loading &&
              filtered.map((model) => {
                const primaryMetric = getPrimaryMetric(model.latest_metrics)
                return (
                  <tr
                    key={model.model_id}
                    className='border-b border-border transition-colors hover:bg-muted/50'
                  >
                    <td className='px-4 py-3'>
                      <div>
                        <span className='font-medium'>{model.model_name}</span>
                        {model.latest_version && model.latest_version > 1 && (
                          <Badge
                            variant='outline'
                            className='ml-2 text-[10px] font-normal'
                          >
                            v{model.latest_version}
                          </Badge>
                        )}
                      </div>
                    </td>
                    <td className='px-4 py-3'>
                      <Badge
                        variant='outline'
                        className={cn(
                          'text-[10px] font-normal',
                          MODEL_TYPE_COLORS[model.model_type]
                        )}
                      >
                        {model.model_type === 'classification'
                          ? 'Classification'
                          : 'Regression'}
                      </Badge>
                    </td>
                    <td className='px-4 py-3'>
                      <Badge
                        variant='outline'
                        className={cn(
                          'text-[10px] font-normal',
                          ALGORITHM_COLORS[model.algorithm ?? 'auto']
                        )}
                      >
                        {getAlgorithmLabel(model.algorithm ?? 'auto')}
                      </Badge>
                    </td>
                    <td className='px-4 py-3'>
                      <span className='font-mono text-xs text-muted-foreground'>
                        {model.target_column ?? '—'}
                      </span>
                    </td>
                    <td className='px-4 py-3 text-center'>
                      {model.latest_status === 'ready' ? (
                        <Badge
                          variant='secondary'
                          className='gap-1 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                        >
                          <CheckCircle2 className='size-3' />
                          Ready
                        </Badge>
                      ) : model.latest_status === 'failed' ? (
                        <Badge
                          variant='secondary'
                          className='gap-1 bg-destructive/10 text-destructive'
                        >
                          <XCircle className='size-3' />
                          Failed
                        </Badge>
                      ) : (
                        <Badge variant='outline' className='text-[10px]'>
                          {model.latest_status ?? 'unknown'}
                        </Badge>
                      )}
                    </td>
                    <td className='px-4 py-3'>
                      {primaryMetric ? (
                        <div className='text-xs'>
                          <span className='font-mono text-muted-foreground'>
                            {primaryMetric.key}
                          </span>
                          <span className='ml-1 font-semibold'>
                            {primaryMetric.value}
                          </span>
                        </div>
                      ) : (
                        <span className='text-xs text-muted-foreground'>—</span>
                      )}
                    </td>
                    <td className='px-4 py-3 text-right'>
                      <span className='font-mono text-xs text-muted-foreground'>
                        {model.training_rows != null
                          ? model.training_rows.toLocaleString()
                          : '—'}
                      </span>
                    </td>
                    <td className='px-4 py-3'>
                      <span className='text-xs text-muted-foreground'>
                        {model.created_at
                          ? new Date(model.created_at).toLocaleDateString()
                          : '—'}
                      </span>
                    </td>
                    <td className='px-4 py-3'>
                      <div className='flex items-center justify-end'>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant='ghost'
                              size='sm'
                              className='h-7 w-7 p-0'
                            >
                              <MoreHorizontal className='size-4' />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align='end'>
                            <DropdownMenuItem
                              onClick={() => setDetailDialog(model)}
                            >
                              <Pencil className='mr-2 size-3.5' />
                              View Details
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className='text-destructive focus:text-destructive'
                              onClick={() => setDeleteDialog(model)}
                            >
                              <Trash2 className='mr-2 size-3.5' />
                              Delete Model
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </td>
                  </tr>
                )
              })}
          </tbody>
        </table>
      </SimpleTableViewport>

      {/* Train Model Dialog */}
      <Dialog open={trainDialogOpen} onOpenChange={setTrainDialogOpen}>
        <DialogContent className='grid max-h-[88vh] grid-rows-[auto_auto_minmax(0,1fr)_auto_auto] overflow-hidden p-0 sm:max-w-[760px]'>
          <DialogHeader className='px-6 pt-6 pb-4'>
            <DialogTitle>Train New ML Model</DialogTitle>
            <DialogDescription className='max-w-[620px]'>
              Train a classification or regression model using data from a SQL
              query. The model will be stored in StarRocks and can be used for
              predictions via model aliases.
            </DialogDescription>
          </DialogHeader>
          <Separator />
          <div className='min-h-0 overflow-y-auto px-6 py-5'>
            <div className='flex flex-col gap-6'>
              <section className='flex flex-col gap-3'>
                <div>
                  <h3 className='text-sm font-medium'>Model Setup</h3>
                  <p className='text-xs text-muted-foreground'>
                    Name the model and choose the training strategy.
                  </p>
                </div>
                <div className='grid gap-4 md:grid-cols-2 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)_minmax(0,1fr)]'>
                  <div className='flex min-w-0 flex-col gap-2'>
                    <Label htmlFor='model-name'>Model Name</Label>
                    <Input
                      id='model-name'
                      placeholder='e.g. churn_predictor'
                      value={trainForm.model_name}
                      onChange={(e) =>
                        setTrainForm((f) => ({
                          ...f,
                          model_name: e.target.value,
                        }))
                      }
                    />
                  </div>
                  <div className='flex min-w-0 flex-col gap-2'>
                    <Label htmlFor='model-type'>Model Type</Label>
                    <Select
                      value={trainForm.model_type}
                      onValueChange={(v) =>
                        setTrainForm((f) => ({
                          ...f,
                          model_type: v as ModelType,
                          algorithm:
                            v === 'regression' && f.algorithm === 'logistic'
                              ? 'auto'
                              : f.algorithm,
                        }))
                      }
                    >
                      <SelectTrigger id='model-type' className='w-full'>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {MODEL_TYPES.map((mt) => (
                          <SelectItem key={mt.value} value={mt.value}>
                            {mt.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className='flex min-w-0 flex-col gap-2 md:col-span-2 lg:col-span-1'>
                    <Label htmlFor='algorithm'>Algorithm</Label>
                    <Select
                      value={trainForm.algorithm}
                      onValueChange={(v) =>
                        setTrainForm((f) => ({
                          ...f,
                          algorithm: v as Algorithm,
                        }))
                      }
                    >
                      <SelectTrigger id='algorithm' className='w-full'>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ALGORITHMS.map((algo) => (
                          <SelectItem key={algo.value} value={algo.value}>
                            <span className='flex flex-col gap-0.5'>
                              <span>{algo.label}</span>
                              <span className='text-xs text-muted-foreground'>
                                {algo.description}
                              </span>
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </section>

              <section className='flex flex-col gap-3'>
                <div>
                  <h3 className='text-sm font-medium'>Training Data</h3>
                  <p className='text-xs text-muted-foreground'>
                    Define the query, target, and predictors used for training.
                  </p>
                </div>
                <div className='flex flex-col gap-4'>
                  <div className='flex min-w-0 flex-col gap-2'>
                    <Label htmlFor='training-sql'>Training SQL Query</Label>
                    <Textarea
                      id='training-sql'
                      placeholder='SELECT feature1, feature2, target_column FROM my_table WHERE ...'
                      value={trainForm.training_sql}
                      onChange={(e) =>
                        setTrainForm((f) => ({
                          ...f,
                          training_sql: e.target.value,
                        }))
                      }
                      rows={5}
                      className='min-h-32 font-mono text-xs leading-relaxed'
                    />
                    <p className='text-xs text-muted-foreground'>
                      Must return the target column and all feature columns.
                    </p>
                  </div>

                  <div className='grid gap-4 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)_minmax(8rem,0.7fr)]'>
                    <div className='flex min-w-0 flex-col gap-2'>
                      <Label htmlFor='target-column'>Target Column</Label>
                      <Input
                        id='target-column'
                        placeholder='e.g. churned'
                        value={trainForm.target_column}
                        onChange={(e) =>
                          setTrainForm((f) => ({
                            ...f,
                            target_column: e.target.value,
                          }))
                        }
                      />
                      <p className='text-xs text-muted-foreground'>
                        Column to predict.
                      </p>
                    </div>
                    <div className='flex min-w-0 flex-col gap-2'>
                      <Label htmlFor='feature-columns'>
                        Feature Columns (optional)
                      </Label>
                      <Input
                        id='feature-columns'
                        placeholder='e.g. age, tenure, usage'
                        value={trainForm.feature_columns}
                        onChange={(e) =>
                          setTrainForm((f) => ({
                            ...f,
                            feature_columns: e.target.value,
                          }))
                        }
                      />
                      <p className='text-xs text-muted-foreground'>
                        Comma-separated. Empty uses all columns except target.
                      </p>
                    </div>
                    <div className='flex min-w-0 flex-col gap-2'>
                      <Label htmlFor='test-size'>Test Size</Label>
                      <Input
                        id='test-size'
                        type='number'
                        min={0}
                        max={0.99}
                        step={0.05}
                        value={trainForm.test_size}
                        onChange={(e) =>
                          setTrainForm((f) => ({
                            ...f,
                            test_size: e.target.value,
                          }))
                        }
                      />
                      <p className='text-xs text-muted-foreground'>
                        Fraction held out.
                      </p>
                    </div>
                  </div>
                </div>
              </section>

              <section className='flex flex-col gap-3'>
                <div>
                  <h3 className='text-sm font-medium'>Scale Planning</h3>
                  <p className='text-xs text-muted-foreground'>
                    Scope the training database and optional tuning parameters.
                  </p>
                </div>
                <div className='grid gap-4 md:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]'>
                  <div className='flex min-w-0 flex-col gap-2'>
                    <Label htmlFor='database-name'>Database (optional)</Label>
                    <Input
                      id='database-name'
                      placeholder='e.g. analytics'
                      value={trainForm.database_name}
                      onChange={(e) =>
                        setTrainForm((f) => ({
                          ...f,
                          database_name: e.target.value,
                        }))
                      }
                    />
                    <p className='text-xs text-muted-foreground'>
                      Uses the current context when empty.
                    </p>
                  </div>
                  <div className='flex min-w-0 flex-col gap-2'>
                    <Label htmlFor='hyperparameters'>
                      Hyperparameters (JSON, optional)
                    </Label>
                    <Textarea
                      id='hyperparameters'
                      placeholder='{"n_estimators": 100, "max_depth": 5}'
                      value={trainForm.hyperparameters}
                      onChange={(e) =>
                        setTrainForm((f) => ({
                          ...f,
                          hyperparameters: e.target.value,
                        }))
                      }
                      rows={3}
                      className='min-h-20 font-mono text-xs leading-relaxed'
                    />
                  </div>
                </div>
              </section>
            </div>
          </div>
          <Separator />
          <DialogFooter className='px-6 pt-4 pb-6'>
            <Button variant='outline' onClick={() => setTrainDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleTrainModel} disabled={submitting}>
              {submitting && (
                <Loader2 className='mr-2 size-3.5 animate-spin' />
              )}
              Train Model
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Model Detail Dialog */}
      <Dialog
        open={!!detailDialog}
        onOpenChange={(open) => !open && setDetailDialog(null)}
      >
        <DialogContent className='max-w-lg'>
          <DialogHeader>
            <DialogTitle>Model Details</DialogTitle>
            <DialogDescription>
              Detailed information about &ldquo;{detailDialog?.model_name}&rdquo;
            </DialogDescription>
          </DialogHeader>
          {detailDialog && (
            <div className='space-y-3 py-2'>
              <div className='grid grid-cols-2 gap-3 text-sm'>
                <div>
                  <span className='text-muted-foreground'>Model ID</span>
                  <p className='font-mono text-xs'>{detailDialog.model_id}</p>
                </div>
                <div>
                  <span className='text-muted-foreground'>Version</span>
                  <p className='font-medium'>
                    v{detailDialog.latest_version ?? 1}
                  </p>
                </div>
                <div>
                  <span className='text-muted-foreground'>Type</span>
                  <p>
                    <Badge
                      variant='outline'
                      className={cn(
                        'text-[10px] font-normal',
                        MODEL_TYPE_COLORS[detailDialog.model_type]
                      )}
                    >
                      {detailDialog.model_type}
                    </Badge>
                  </p>
                </div>
                <div>
                  <span className='text-muted-foreground'>Algorithm</span>
                  <p>
                    <Badge
                      variant='outline'
                      className={cn(
                        'text-[10px] font-normal',
                        ALGORITHM_COLORS[detailDialog.algorithm ?? 'auto']
                      )}
                    >
                      {getAlgorithmLabel(detailDialog.algorithm ?? 'auto')}
                    </Badge>
                  </p>
                </div>
                <div>
                  <span className='text-muted-foreground'>Target Column</span>
                  <p className='font-mono text-xs'>
                    {detailDialog.target_column ?? '—'}
                  </p>
                </div>
                <div>
                  <span className='text-muted-foreground'>Training Rows</span>
                  <p className='font-mono text-xs'>
                    {detailDialog.training_rows?.toLocaleString() ?? '—'}
                  </p>
                </div>
                <div>
                  <span className='text-muted-foreground'>Created By</span>
                  <p className='text-xs'>{detailDialog.created_by ?? '—'}</p>
                </div>
                <div>
                  <span className='text-muted-foreground'>Created At</span>
                  <p className='text-xs'>
                    {detailDialog.created_at
                      ? new Date(detailDialog.created_at).toLocaleString()
                      : '—'}
                  </p>
                </div>
              </div>

              {detailDialog.feature_columns &&
                detailDialog.feature_columns.length > 0 && (
                  <div>
                    <span className='text-xs font-medium uppercase tracking-wide text-muted-foreground'>
                      Feature Columns ({detailDialog.feature_columns.length})
                    </span>
                    <div className='mt-1 flex flex-wrap gap-1'>
                      {detailDialog.feature_columns.map((col) => (
                        <Badge
                          key={col}
                          variant='outline'
                          className='font-mono text-[10px]'
                        >
                          {col}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}

              {detailDialog.latest_metrics &&
                Object.keys(detailDialog.latest_metrics).length > 0 && (
                  <div>
                    <span className='text-xs font-medium uppercase tracking-wide text-muted-foreground'>
                      Metrics
                    </span>
                    <div className='mt-1 grid grid-cols-2 gap-2 rounded-md border p-2'>
                      {Object.entries(detailDialog.latest_metrics).map(
                        ([key, value]) => (
                          <div key={key} className='flex justify-between text-xs'>
                            <span className='font-mono text-muted-foreground'>
                              {key}
                            </span>
                            <span className='font-medium'>
                              {formatMetricValue(key, value)}
                            </span>
                          </div>
                        )
                      )}
                    </div>
                  </div>
                )}

              {detailDialog.training_sql && (
                <div>
                  <span className='text-xs font-medium uppercase tracking-wide text-muted-foreground'>
                    Training SQL
                  </span>
                  <pre className='mt-1 max-h-32 overflow-auto rounded-md border bg-muted p-2 font-mono text-xs'>
                    {detailDialog.training_sql}
                  </pre>
                </div>
              )}

              {detailDialog.hyperparameters &&
                Object.keys(detailDialog.hyperparameters).length > 0 && (
                  <div>
                    <span className='text-xs font-medium uppercase tracking-wide text-muted-foreground'>
                      Hyperparameters
                    </span>
                    <pre className='mt-1 rounded-md border bg-muted p-2 font-mono text-xs'>
                      {JSON.stringify(detailDialog.hyperparameters, null, 2)}
                    </pre>
                  </div>
                )}
            </div>
          )}
          <DialogFooter>
            <Button variant='outline' onClick={() => setDetailDialog(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog
        open={!!deleteDialog}
        onOpenChange={(open) => !open && setDeleteDialog(null)}
      >
        <DialogContent className='max-w-md'>
          <DialogHeader>
            <DialogTitle>Confirm Deletion</DialogTitle>
            <DialogDescription>
              Delete ML model &ldquo;{deleteDialog?.model_name}&rdquo;? This
              will permanently remove the model and all its versions. Any
              aliases pointing to this model will also be removed.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant='outline' onClick={() => setDeleteDialog(null)}>
              Cancel
            </Button>
            <Button
              variant='destructive'
              onClick={handleDelete}
              disabled={submitting}
            >
              {submitting && (
                <Loader2 className='mr-2 size-3.5 animate-spin' />
              )}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
