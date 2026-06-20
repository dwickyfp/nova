export type CompletionItem = {
  label: string
  type: string
  insert_text?: string
  detail?: string
  size?: number | null
  last_modified?: string | null
}

export type CompletionResponse = {
  items: CompletionItem[]
}

export type StageCompletionContext =
  | {
      kind: 'stage'
      prefix: string
    }
  | {
      kind: 'stage_path'
      stage: string
      folder: string
      prefix: string
    }

export function extractStageCompletionContext(
  textUntilPosition: string
): StageCompletionContext | null {
  const stagePathMatch = textUntilPosition.match(
    /@([A-Za-z_][A-Za-z0-9_-]*)\.([A-Za-z0-9_./-]*)$/
  )
  if (stagePathMatch) {
    const stage = stagePathMatch[1]
    const path = stagePathMatch[2] ?? ''
    const segments = path.split(/[./]/)
    const prefix = segments.pop() ?? ''
    return {
      kind: 'stage_path',
      stage,
      folder: segments.filter(Boolean).join('.'),
      prefix,
    }
  }

  const stageMatch = textUntilPosition.match(/@([A-Za-z0-9_-]*)$/)
  if (stageMatch) {
    return {
      kind: 'stage',
      prefix: stageMatch[1] ?? '',
    }
  }

  return null
}

export function getStageCompletionInsertText(item: CompletionItem): string {
  return item.insert_text ?? item.label
}

export function shouldTriggerStageSuggestions(item: CompletionItem): boolean {
  return item.type === 'stage' || item.type === 'stage_folder'
}

export function formatStageCompletionDetail(item: CompletionItem): string {
  if (item.type === 'stage') return item.detail ?? 'Stage'
  if (item.type === 'stage_folder') return item.detail ?? 'Folder'

  const details = [item.detail ?? 'Stage file']
  if (typeof item.size === 'number') {
    details.push(formatBytes(item.size))
  }
  return details.join(' · ')
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}
