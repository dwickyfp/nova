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

export type StageCompletionKind = 'stage' | 'folder' | 'file' | 'other'

export function buildStageCompletionPath(
  database: string,
  context: StageCompletionContext
): string {
  const params = new URLSearchParams({
    kind: context.kind === 'stage' ? 'stage' : 'stage_file',
    database,
    prefix: context.prefix,
  })
  if (context.kind === 'stage_path') {
    params.set('stage', context.stage)
    params.set('folder', context.folder)
  }
  return `/query/completions?${params.toString()}`
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

export function getStageCompletionKind(
  item: CompletionItem
): StageCompletionKind {
  if (item.type === 'stage') return 'stage'
  if (item.type === 'stage_folder') return 'folder'
  if (item.type === 'stage_file') return 'file'
  return 'other'
}

export function getStageCompletionSortText(item: CompletionItem): string {
  const kind = getStageCompletionKind(item)
  const rank =
    kind === 'folder' ? '0' : kind === 'stage' ? '1' : kind === 'file' ? '2' : '3'
  return `${rank}-${item.label.toLowerCase()}`
}

export function getStageCompletionReplacementRange(
  lineNumber: number,
  column: number,
  context: StageCompletionContext
) {
  return {
    startLineNumber: lineNumber,
    startColumn: Math.max(1, column - context.prefix.length),
    endLineNumber: lineNumber,
    endColumn: column,
  }
}

export function getStageFileExtension(item: CompletionItem): string | null {
  if (item.type !== 'stage_file') return null
  const fileName = item.label.split('/').pop() ?? item.label
  const extensionIndex = fileName.lastIndexOf('.')
  if (extensionIndex <= 0 || extensionIndex === fileName.length - 1) return null
  return fileName.slice(extensionIndex + 1).toUpperCase()
}

export function formatStageCompletionDetail(item: CompletionItem): string {
  if (item.type === 'stage') return item.detail ?? 'Stage'
  if (item.type === 'stage_folder') return item.detail ?? 'Folder'

  const extension = getStageFileExtension(item)
  const details = [extension ? `${extension} file` : item.detail ?? 'Stage file']
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
