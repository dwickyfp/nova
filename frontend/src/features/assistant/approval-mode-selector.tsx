import { ShieldCheck } from 'lucide-react'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { ApprovalMode } from './use-assistant-turn'

export type ApprovalModeSelectorProps = {
  selected: ApprovalMode
  onSelect: (mode: ApprovalMode) => void
  /** True while a mode change is being persisted. */
  settling?: boolean
  disabled?: boolean
  /** Horizontal alignment of the dropdown relative to the trigger. */
  align?: 'start' | 'center' | 'end'
}

const MODE_LABEL: Record<ApprovalMode, string> = {
  ask: 'Need approval',
  allow_read_only: 'Always allow read-only',
}

const MODE_DESCRIPTION: Record<ApprovalMode, string> = {
  ask: 'Read-only queries ask before running.',
  allow_read_only: 'Read-only queries run without asking. Writes still ask.',
}

/**
 * Approval-mode picker in the composer footer. It sets the conversation's
 * read-only grant, so a query does not need a per-call approval card; `ask`
 * clears it. The trigger state is the grant itself, so it can never show a mode
 * the engine would not honour.
 *
 * Labeled only by its icon, matching the model selector beside it, with the
 * current mode as the value. The full text lives in the dropdown items and in
 * the trigger's accessible name.
 */
export function ApprovalModeSelector({
  selected,
  onSelect,
  settling,
  disabled,
  align = 'start',
}: ApprovalModeSelectorProps) {
  return (
    <Select
      value={selected}
      onValueChange={(value) => onSelect(value as ApprovalMode)}
      disabled={disabled || settling}
    >
      <SelectTrigger
        size='sm'
        className='h-7 max-w-[12rem] min-w-0 gap-1.5 border-none bg-transparent px-1.5 text-xs shadow-none focus-visible:ring-0 dark:bg-transparent'
        aria-label={`Approval mode: ${MODE_LABEL[selected]}`}
      >
        <ShieldCheck aria-hidden='true' className='size-3.5 shrink-0 text-muted-foreground' />
        <SelectValue>{MODE_LABEL[selected]}</SelectValue>
      </SelectTrigger>
      <SelectContent align={align} className='max-w-[16rem]'>
        {(Object.keys(MODE_LABEL) as ApprovalMode[]).map((mode) => (
          <SelectItem key={mode} value={mode}>
            <span className='flex flex-col items-start'>
              <span>{MODE_LABEL[mode]}</span>
              <span className='text-xs text-muted-foreground'>{MODE_DESCRIPTION[mode]}</span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
