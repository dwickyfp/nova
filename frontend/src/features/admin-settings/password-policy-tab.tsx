import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertCircle, KeyRound, RotateCcw, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { LoadingOverlay } from '@/components/ui/loading-overlay'
import { StatusBadge } from '@/components/ui/status-badge'
import { Switch } from '@/components/ui/switch'
import {
  PASSWORD_POLICY_DEFAULTS,
  fetchPasswordPolicy,
  parsePolicyNumber,
  updatePasswordPolicy,
  type PasswordPolicy,
} from './api'

type PolicyField = {
  key: keyof PasswordPolicy
  label: string
  hint?: string
  suffix?: string
}

const EXPIRY_FIELDS: PolicyField[] = [
  {
    key: 'password_lifetime',
    label: 'Password lifetime',
    hint: 'Days before a password must be changed. 0 disables expiry.',
    suffix: 'days',
  },
  {
    key: 'password_history',
    label: 'Password history',
    hint: 'Previous passwords remembered and rejected on reuse.',
    suffix: 'passwords',
  },
]

const LOCKOUT_FIELDS: PolicyField[] = [
  {
    key: 'failed_login_attempts',
    label: 'Failed attempts before lock',
    hint: '0 disables lockout.',
    suffix: 'attempts',
  },
  {
    key: 'password_lock_time',
    label: 'Lock duration',
    suffix: 'minutes',
  },
]

const COMPLEXITY_FIELDS: PolicyField[] = [
  { key: 'validate_password_length', label: 'Minimum length' },
  { key: 'validate_password_mixed_case_count', label: 'Mixed case characters' },
  { key: 'validate_password_number_count', label: 'Digits' },
  { key: 'validate_password_special_char_count', label: 'Special characters' },
]

function policyToDraft(policy: PasswordPolicy): Record<string, string> {
  const draft: Record<string, string> = {}
  for (const [key, value] of Object.entries(policy)) {
    if (key === 'validate_password') continue
    draft[key] = value == null ? '' : String(value)
  }
  return draft
}

export function PasswordPolicyTab() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [validatePassword, setValidatePassword] = useState(false)

  const policyQuery = useQuery({
    queryKey: ['password-policy'],
    queryFn: fetchPasswordPolicy,
  })

  useEffect(() => {
    if (policyQuery.error) {
      toast.error('Failed to load password policy', {
        description: policyQuery.error.message,
      })
    }
  }, [policyQuery.error])

  useEffect(() => {
    if (policyQuery.data) {
      setDraft(policyToDraft(policyQuery.data))
      setValidatePassword(policyQuery.data.validate_password)
    }
  }, [policyQuery.data])

  const saveMutation = useMutation({
    mutationFn: (policy: PasswordPolicy) => updatePasswordPolicy(policy),
    onSuccess: () => {
      toast.success('Password policy saved')
      queryClient.invalidateQueries({ queryKey: ['password-policy'] })
    },
    onError: (err: Error) =>
      toast.error('Could not save password policy', { description: err.message }),
  })

  if (policyQuery.isLoading) {
    return <LoadingOverlay label='Loading password policy...' />
  }

  if (policyQuery.isError) {
    return (
      <EmptyState
        variant='error'
        icon={AlertCircle}
        title='Could not load the password policy'
        description='The variables module did not respond. It may not be deployed on this server yet.'
        action={
          <Button variant='outline' size='sm' onClick={() => void policyQuery.refetch()}>
            Retry
          </Button>
        }
      />
    )
  }

  const setField = (key: keyof PasswordPolicy, value: string) => {
    setDraft((prev) => ({ ...prev, [key]: value }))
  }

  const handleSave = () => {
    const policy: PasswordPolicy = {
      validate_password: validatePassword,
      password_lifetime: parsePolicyNumber(draft.password_lifetime ?? ''),
      password_history: parsePolicyNumber(draft.password_history ?? ''),
      failed_login_attempts: parsePolicyNumber(draft.failed_login_attempts ?? ''),
      password_lock_time: parsePolicyNumber(draft.password_lock_time ?? ''),
      validate_password_length: parsePolicyNumber(draft.validate_password_length ?? ''),
      validate_password_mixed_case_count: parsePolicyNumber(
        draft.validate_password_mixed_case_count ?? ''
      ),
      validate_password_number_count: parsePolicyNumber(
        draft.validate_password_number_count ?? ''
      ),
      validate_password_special_char_count: parsePolicyNumber(
        draft.validate_password_special_char_count ?? ''
      ),
    }
    saveMutation.mutate(policy)
  }

  const renderFields = (fields: PolicyField[]) => (
    <div className='grid gap-4 sm:grid-cols-2'>
      {fields.map((field) => (
        <div key={field.key} className='grid gap-1.5'>
          <Label htmlFor={`policy-${field.key}`}>{field.label}</Label>
          <div className='flex items-center gap-2'>
            <Input
              id={`policy-${field.key}`}
              type='number'
              min={0}
              inputMode='numeric'
              value={draft[field.key] ?? ''}
              onChange={(event) => setField(field.key, event.target.value)}
              className='h-9'
            />
            {field.suffix ? (
              <span className='shrink-0 text-xs text-muted-foreground'>
                {field.suffix}
              </span>
            ) : null}
          </div>
          {field.hint ? (
            <p className='text-xs text-muted-foreground'>{field.hint}</p>
          ) : null}
        </div>
      ))}
    </div>
  )

  return (
    <div className='space-y-6'>
      <section className='rounded-xl border border-border bg-surface-2 p-5'>
        <div className='mb-4 flex items-center gap-2'>
          <KeyRound className='size-4 text-muted-foreground' />
          <h3 className='text-sm font-medium'>Expiration</h3>
        </div>
        {renderFields(EXPIRY_FIELDS)}
      </section>

      <section className='rounded-xl border border-border bg-surface-2 p-5'>
        <div className='mb-4 flex items-center gap-2'>
          <ShieldCheck className='size-4 text-muted-foreground' />
          <h3 className='text-sm font-medium'>Lockout</h3>
        </div>
        {renderFields(LOCKOUT_FIELDS)}
      </section>

      <section className='rounded-xl border border-border bg-surface-2 p-5'>
        <div className='mb-4 flex items-center justify-between gap-2'>
          <div className='flex items-center gap-2'>
            <ShieldCheck className='size-4 text-muted-foreground' />
            <h3 className='text-sm font-medium'>Complexity</h3>
          </div>
          <StatusBadge tone={validatePassword ? 'success' : 'neutral'}>
            {validatePassword ? 'Enforced' : 'Off'}
          </StatusBadge>
        </div>
        <label className='mb-4 flex items-center gap-3'>
          <Switch
            checked={validatePassword}
            onCheckedChange={setValidatePassword}
            aria-label='Enable password validation'
          />
          <span className='text-sm'>Enable password validation</span>
        </label>
        {renderFields(COMPLEXITY_FIELDS)}
      </section>

      <div className='flex flex-wrap items-center gap-2'>
        <Button disabled={saveMutation.isPending} onClick={handleSave}>
          {saveMutation.isPending ? 'Saving...' : 'Save policy'}
        </Button>
        <Button
          variant='outline'
          disabled={saveMutation.isPending}
          onClick={() => {
            setDraft(policyToDraft(PASSWORD_POLICY_DEFAULTS))
            setValidatePassword(PASSWORD_POLICY_DEFAULTS.validate_password)
          }}
        >
          <RotateCcw className='me-1.5 size-3.5' />
          Reset to defaults
        </Button>
      </div>
    </div>
  )
}
