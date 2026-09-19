import { useEffect, useMemo, useRef, useState } from 'react'
import Editor, { type Monaco } from '@monaco-editor/react'
import { Clock, FileCode, Loader2 } from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import { useTheme } from '@/context/theme-provider'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { applyNovaSqlTheme } from './monaco-theme'
import { formatBytes, formatTimestamp } from './file-history-format'
import type { FileVersion, FileVersionResponse, FileVersionsResponse } from './types'

export type FileHistoryDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Workspace entry whose versions are shown; null while no file is open. */
  entryId: string | null
  fileName: string
}

/**
 * Near-fullscreen version history for one worksheet. The left three quarters
 * show a read-only Monaco preview of the selected version (same theme and
 * highlighting as the editor, scrolling on overflow); the right quarter lists
 * every snapshot, newest first, itself scrollable. There is no restore action:
 * the dialog is for inspection only.
 */
export function FileHistoryDialog({
  open,
  onOpenChange,
  entryId,
  fileName,
}: FileHistoryDialogProps) {
  const { resolvedTheme } = useTheme()
  const monacoRef = useRef<Monaco | null>(null)
  const [monacoReady, setMonacoReady] = useState(false)
  const [versions, setVersions] = useState<FileVersion[]>([])
  const [loadingList, setLoadingList] = useState(false)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [content, setContent] = useState('')
  const [loadingContent, setLoadingContent] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Load the version list whenever the dialog opens for a file. Resetting the
  // selection on open means the preview never shows the previous file's
  // snapshot while the new list is in flight.
  useEffect(() => {
    if (!open || !entryId) return
    let cancelled = false
    setLoadingList(true)
    setError(null)
    setVersions([])
    setSelectedVersion(null)
    setContent('')
    api
      .get<FileVersionsResponse>(`/workspaces/files/${encodeURIComponent(entryId)}/versions`)
      .then((response) => {
        if (cancelled) return
        setVersions(response.versions)
        setSelectedVersion(response.versions[0]?.version ?? null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Failed to load history')
      })
      .finally(() => {
        if (!cancelled) setLoadingList(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, entryId])

  // Fetch the selected version's content. Keyed on version so clicking a row
  // never shows the previous version's text while the new one loads.
  useEffect(() => {
    if (!open || !entryId || selectedVersion === null) return
    let cancelled = false
    setLoadingContent(true)
    api
      .get<FileVersionResponse>(
        `/workspaces/files/${encodeURIComponent(entryId)}/versions/${selectedVersion}`
      )
      .then((response) => {
        if (cancelled) return
        setContent(response.content)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Failed to load version')
      })
      .finally(() => {
        if (!cancelled) setLoadingContent(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, entryId, selectedVersion])

  const handleMount = useMemo(
    () => (_editor: unknown, monaco: Monaco) => {
      monacoRef.current = monaco
      setMonacoReady(true)
      applyNovaSqlTheme(monaco, resolvedTheme === 'dark')
    },
    [resolvedTheme]
  )

  // `onMount` fires once, so a theme toggle after the dialog opens would leave
  // the preview on the theme selected at mount. Re-apply on every change; the
  // monacoReady dependency covers the first paint, where the namespace only
  // exists once onMount has run.
  useEffect(() => {
    const monaco = monacoRef.current
    if (!monaco) return
    const frame = requestAnimationFrame(() => {
      applyNovaSqlTheme(monaco, resolvedTheme === 'dark')
    })
    return () => cancelAnimationFrame(frame)
  }, [resolvedTheme, monacoReady])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton
        className='flex h-[88vh] w-[95vw] max-w-[95vw] flex-col gap-0 overflow-hidden p-0 sm:max-w-[95vw]'
      >
        <DialogHeader className='border-b px-4 py-3 text-start'>
          <DialogTitle className='flex items-center gap-2 text-base'>
            <Clock aria-hidden='true' className='size-4 text-muted-foreground' />
            Version history
          </DialogTitle>
          <DialogDescription className='truncate'>{fileName}</DialogDescription>
        </DialogHeader>

        {/* 3/4 preview : 1/4 list. `min-h-0` on both children keeps each pane
            scrolling internally instead of growing the dialog. */}
        <div className='grid min-h-0 flex-1 grid-cols-4 overflow-hidden'>
          <div className='col-span-3 flex min-h-0 flex-col border-r'>
            {loadingContent ? (
              <div className='flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground'>
                <Loader2 aria-hidden='true' className='size-4 animate-spin' />
                Loading version…
              </div>
            ) : selectedVersion === null ? (
              <div className='flex flex-1 items-center justify-center text-sm text-muted-foreground'>
                Select a version to preview it.
              </div>
            ) : (
              <Editor
                height='100%'
                language='sql'
                theme={resolvedTheme === 'dark' ? 'nova-dark' : 'nova-light'}
                value={content}
                onMount={handleMount}
                options={{
                  readOnly: true,
                  domReadOnly: true,
                  minimap: { enabled: false },
                  fontSize: 12,
                  fontFamily:
                    "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace",
                  fontLigatures: true,
                  lineHeight: 20,
                  automaticLayout: true,
                  wordWrap: 'on',
                  scrollBeyondLastLine: false,
                  renderLineHighlight: 'none',
                  overviewRulerBorder: false,
                  hideCursorInOverviewRuler: true,
                  padding: { top: 10, bottom: 12 },
                }}
              />
            )}
          </div>

          <div className='col-span-1 flex min-h-0 flex-col bg-surface-1'>
            <div className='flex items-center justify-between border-b px-3 py-2'>
              <span className='text-xs font-medium text-muted-foreground'>Versions</span>
              <span className='text-xs text-muted-foreground'>{versions.length}</span>
            </div>
            <ScrollArea className='h-0 min-h-0 flex-1'>
              {loadingList ? (
                <p className='p-3 text-xs text-muted-foreground'>Loading…</p>
              ) : error ? (
                <p className='p-3 text-xs text-destructive'>{error}</p>
              ) : versions.length === 0 ? (
                <p className='p-3 text-xs text-muted-foreground'>
                  No saved versions yet. Versions appear after the file is saved with changes.
                </p>
              ) : (
                <ul className='flex flex-col p-1'>
                  {versions.map((version) => {
                    const active = version.version === selectedVersion
                    return (
                      <li key={version.id}>
                        <button
                          type='button'
                          onClick={() => setSelectedVersion(version.version)}
                          className={cn(
                            'flex w-full flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-start transition-colors',
                            active ? 'bg-accent text-accent-foreground' : 'hover:bg-muted'
                          )}
                        >
                          <span className='flex items-center gap-1.5 text-xs font-medium'>
                            <FileCode aria-hidden='true' className='size-3 shrink-0' />v
                            {version.version}
                          </span>
                          <span className='truncate text-[0.7rem] text-muted-foreground'>
                            {formatTimestamp(version.created_at)}
                          </span>
                          <span className='text-[0.7rem] text-muted-foreground'>
                            {formatBytes(version.size_bytes)}
                          </span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </ScrollArea>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
