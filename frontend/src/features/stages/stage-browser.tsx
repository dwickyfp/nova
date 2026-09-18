import { Fragment, useCallback, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ClipboardCopy,
  FileText,
  FolderOpen,
  RefreshCw,
  Trash2,
  Upload,
} from 'lucide-react'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { LoadingLines, RefreshBanner } from '@/components/ui/loading-overlay'
import { PageHeader } from '@/components/ui/page-header'
import { StatusBadge } from '@/components/ui/status-badge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import {
  deleteStageFile,
  fetchStageFiles,
  formatBytes,
  queryRefFor,
  stageFileDownloadHeaders,
  stageFileDownloadUrl,
  uploadStageFile,
  type Stage,
  type StageFile,
} from './api'

type StageBrowserProps = {
  stage: Stage
  onBack: () => void
}

export function StageBrowser({ stage, onBack }: StageBrowserProps) {
  const queryClient = useQueryClient()
  const [path, setPath] = useState<string[]>([])
  const [uploadOpen, setUploadOpen] = useState(false)
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [dropTarget, setDropTarget] = useState<StageFile | null>(null)
  const [copiedRef, setCopiedRef] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const prefix = path.join('/')

  const filesQuery = useQuery({
    queryKey: ['stage-files', stage.id, prefix],
    queryFn: () => fetchStageFiles(stage.id, prefix),
  })

  const uploadMutation = useMutation({
    mutationFn: async (files: File[]) => {
      for (const file of files) {
        await uploadStageFile(stage.id, file, prefix)
      }
    },
    onSuccess: () => {
      toast.success(`Uploaded ${pendingFiles.length} file${pendingFiles.length === 1 ? '' : 's'}`)
      setUploadOpen(false)
      setPendingFiles([])
      queryClient.invalidateQueries({ queryKey: ['stage-files', stage.id] })
    },
    onError: (err: Error) => toast.error('Upload failed', { description: err.message }),
  })

  const deleteMutation = useMutation({
    mutationFn: (file: StageFile) => {
      const filename = path.length > 0 ? `${prefix}/${file.name}` : file.name
      return deleteStageFile(stage.id, filename)
    },
    onSuccess: () => {
      toast.success('File deleted')
      setDropTarget(null)
      queryClient.invalidateQueries({ queryKey: ['stage-files', stage.id] })
    },
    onError: (err: Error) => toast.error('Could not delete file', { description: err.message }),
  })

  const openFile = useCallback(
    (file: StageFile) => {
      if (file.is_dir) {
        setPath((prev) => [...prev, file.name])
        return
      }
      const filename = prefix ? `${prefix}/${file.name}` : file.name
      fetch(stageFileDownloadUrl(stage.id, filename), {
        headers: stageFileDownloadHeaders(),
      })
        .then(async (res) => {
          if (!res.ok) throw new Error('Download failed')
          const blob = await res.blob()
          const url = URL.createObjectURL(blob)
          const anchor = document.createElement('a')
          anchor.href = url
          anchor.download = file.name
          anchor.click()
          URL.revokeObjectURL(url)
        })
        .catch((err: Error) => toast.error('Download failed', { description: err.message }))
    },
    [prefix, stage.id]
  )

  const copyQueryRef = useCallback(
    (file: StageFile) => {
      const filename = prefix ? `${prefix}/${file.name}` : file.name
      const ref = queryRefFor(stage, filename)
      void navigator.clipboard.writeText(ref).then(() => {
        setCopiedRef(file.name)
        toast.success('Query reference copied', { description: ref })
        window.setTimeout(() => setCopiedRef(null), 1500)
      })
    },
    [prefix, stage]
  )

  const files = filesQuery.data?.files ?? []
  const directories = files.filter((file) => file.is_dir)
  const regularFiles = files.filter((file) => !file.is_dir)
  const totalBytes = regularFiles.reduce((sum, file) => sum + file.size, 0)

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault()
    setDragOver(false)
    const dropped = Array.from(event.dataTransfer.files)
    if (dropped.length) setPendingFiles((prev) => [...prev, ...dropped])
  }

  return (
    <div className='flex min-h-0 flex-1 flex-col gap-6'>
      <div>
        <Button variant='ghost' size='sm' className='-ms-2 mb-2' onClick={onBack}>
          <ArrowLeft className='me-1.5 size-4' />
          All stages
        </Button>
        <PageHeader
          title={`@${stage.name}`}
          description={`${stage.database_name}.${stage.schema_name} · storage connection ${stage.storage_connection}`}
          actions={
            <Button
              size='sm'
              onClick={() => {
                setPendingFiles([])
                setUploadOpen(true)
              }}
            >
              <Upload className='me-1.5 size-4' />
              Upload
            </Button>
          }
        />
      </div>

      <div className='flex flex-wrap items-center justify-between gap-3'>
        <nav aria-label='Stage path' className='flex items-center gap-1 text-sm'>
          <button
            type='button'
            className='text-muted-foreground transition-colors hover:text-foreground'
            onClick={() => setPath([])}
          >
            {stage.name}
          </button>
          {path.map((segment, index) => (
            <Fragment key={`${segment}-${index}`}>
              <span className='text-muted-foreground'>/</span>
              {index === path.length - 1 ? (
                <span className='font-medium'>{segment}</span>
              ) : (
                <button
                  type='button'
                  className='text-muted-foreground transition-colors hover:text-foreground'
                  onClick={() => setPath(path.slice(0, index + 1))}
                >
                  {segment}
                </button>
              )}
            </Fragment>
          ))}
        </nav>
        <div className='flex items-center gap-3 text-xs text-muted-foreground'>
          <span>
            {regularFiles.length} file{regularFiles.length === 1 ? '' : 's'}
            {directories.length > 0
              ? `, ${directories.length} folder${directories.length === 1 ? '' : 's'}`
              : ''}
          </span>
          <span aria-hidden='true'>·</span>
          <span>{formatBytes(totalBytes)}</span>
          <Button
            variant='ghost'
            size='sm'
            className='size-7 p-0 text-muted-foreground'
            aria-label='Refresh file list'
            disabled={filesQuery.isFetching}
            onClick={() => void filesQuery.refetch()}
          >
            <RefreshCw className={cn('size-3.5', filesQuery.isFetching && 'animate-spin')} />
          </Button>
        </div>
      </div>

      <div className='relative min-h-0 flex-1'>
        {filesQuery.isFetching && !filesQuery.isLoading ? (
          <RefreshBanner label='Refreshing files...' />
        ) : null}
        <div className='min-h-0 overflow-auto rounded-lg border border-border bg-background'>
          <table className='w-full'>
            <thead className='sticky top-0 z-10 border-b border-border bg-muted'>
              <tr>
                <th className='px-4 py-3 text-left text-xs font-medium text-muted-foreground'>
                  Name
                </th>
                <th className='w-28 px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                  Size
                </th>
                <th className='w-52 px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                  Last modified
                </th>
                <th className='w-32 px-4 py-3 text-right text-xs font-medium text-muted-foreground'>
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {filesQuery.isLoading ? (
                <tr>
                  <td colSpan={4} className='px-4 py-6'>
                    <LoadingLines rows={6} />
                  </td>
                </tr>
              ) : filesQuery.isError ? (
                <tr>
                  <td colSpan={4} className='px-4 py-6'>
                    <EmptyState
                      variant='error'
                      icon={AlertCircle}
                      title='Could not list stage files'
                      description='The storage connection did not respond. Check the connection and retry.'
                      action={
                        <Button variant='outline' size='sm' onClick={() => void filesQuery.refetch()}>
                          Retry
                        </Button>
                      }
                    />
                  </td>
                </tr>
              ) : files.length === 0 ? (
                <tr>
                  <td colSpan={4} className='px-4 py-6'>
                    <EmptyState
                      icon={FolderOpen}
                      title={path.length > 0 ? 'This folder is empty' : 'This stage has no files yet'}
                      description={
                        path.length > 0
                          ? 'Upload a file here, or go up a level to browse other folders.'
                          : `Upload a file to query it as SELECT * FROM @${stage.name}.your_file.csv`
                      }
                      action={
                        <Button size='sm' onClick={() => setUploadOpen(true)}>
                          <Upload className='me-1.5 size-4' />
                          Upload
                        </Button>
                      }
                    />
                  </td>
                </tr>
              ) : (
                <>
                  {path.length > 0 ? (
                    <tr
                      className='cursor-pointer border-b border-border hover:bg-muted/50'
                      onClick={() => setPath(path.slice(0, -1))}
                    >
                      <td className='px-4 py-3 text-sm text-muted-foreground' colSpan={4}>
                        .. up one level
                      </td>
                    </tr>
                  ) : null}
                  {files.map((file) => (
                    <tr
                      key={`${file.is_dir ? 'dir' : 'file'}-${file.name}`}
                      className='border-b border-border transition-colors hover:bg-muted/50'
                    >
                      <td className='px-4 py-3'>
                        <button
                          type='button'
                          className='flex min-w-0 items-center gap-2 text-left'
                          onClick={() => openFile(file)}
                        >
                          {file.is_dir ? (
                            <FolderOpen className='size-4 shrink-0 text-muted-foreground' />
                          ) : (
                            <FileText className='size-4 shrink-0 text-muted-foreground' />
                          )}
                          <span className='truncate text-sm font-medium'>{file.name}</span>
                          {!file.is_dir ? (
                            <StatusBadge tone='neutral' className='hidden sm:inline-flex'>
                              query
                            </StatusBadge>
                          ) : null}
                        </button>
                      </td>
                      <td className='px-4 py-3 text-right text-xs text-muted-foreground'>
                        {file.is_dir ? '—' : formatBytes(file.size)}
                      </td>
                      <td className='px-4 py-3 text-right text-xs text-muted-foreground whitespace-nowrap'>
                        {file.last_modified
                          ? new Date(file.last_modified).toLocaleDateString(undefined, {
                              year: 'numeric',
                              month: 'short',
                              day: 'numeric',
                            })
                          : '—'}
                      </td>
                      <td className='px-4 py-3 text-right'>
                        {!file.is_dir ? (
                          <div className='flex items-center justify-end gap-1'>
                            <Button
                              variant='ghost'
                              size='sm'
                              className='size-8 p-0 text-muted-foreground'
                              aria-label={
                                copiedRef === file.name
                                  ? `Copied query reference for ${file.name}`
                                  : `Copy query reference for ${file.name}`
                              }
                              onClick={() => copyQueryRef(file)}
                            >
                              {copiedRef === file.name ? (
                                <Check className='size-3.5' />
                              ) : (
                                <ClipboardCopy className='size-3.5' />
                              )}
                            </Button>
                            <Button
                              variant='ghost'
                              size='sm'
                              className='size-8 p-0 text-muted-foreground hover:text-destructive'
                              aria-label={`Delete ${file.name}`}
                              onClick={() => setDropTarget(file)}
                            >
                              <Trash2 className='size-3.5' />
                            </Button>
                          </div>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
        <DialogContent className='sm:max-w-lg'>
          <DialogHeader>
            <DialogTitle>Upload to @{stage.name}</DialogTitle>
            <DialogDescription>
              Files land in {prefix ? `/${prefix}/` : 'the stage root'}. Query them with{' '}
              <code className='rounded bg-muted px-1 py-0.5 text-xs'>
                SELECT * FROM @{stage.name}.your_file.csv
              </code>
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-4 py-2'>
            <div
              onDragOver={(event) => {
                event.preventDefault()
                setDragOver(true)
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={cn(
                'flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors',
                dragOver
                  ? 'border-primary bg-primary/5'
                  : 'border-border hover:border-muted-foreground/50'
              )}
            >
              <Upload
                className={cn('mb-3 size-7', dragOver ? 'text-primary' : 'text-muted-foreground')}
              />
              <p className='text-sm font-medium'>Drop files here or click to browse</p>
              <p className='mt-1 text-xs text-muted-foreground'>CSV, JSON, Parquet, ORC, Avro</p>
              <input
                ref={fileInputRef}
                type='file'
                multiple
                className='hidden'
                onChange={(event) => {
                  const selected = Array.from(event.target.files ?? [])
                  if (selected.length) setPendingFiles((prev) => [...prev, ...selected])
                }}
              />
            </div>

            {pendingFiles.length > 0 ? (
              <div className='space-y-2'>
                <p className='text-sm font-medium'>Files to upload ({pendingFiles.length})</p>
                <ul className='max-h-40 space-y-1 overflow-y-auto rounded-lg border border-border p-2'>
                  {pendingFiles.map((file, index) => (
                    <li
                      key={`${file.name}-${index}`}
                      className='flex items-center justify-between rounded px-2 py-1 text-sm'
                    >
                      <span className='flex min-w-0 items-center gap-2'>
                        <FileText className='size-3.5 shrink-0 text-muted-foreground' />
                        <span className='truncate'>{file.name}</span>
                      </span>
                      <span className='flex items-center gap-2'>
                        <span className='text-xs text-muted-foreground'>
                          {formatBytes(file.size)}
                        </span>
                        <Button
                          variant='ghost'
                          size='sm'
                          className='size-6 p-0 text-muted-foreground hover:text-destructive'
                          aria-label={`Remove ${file.name}`}
                          onClick={() =>
                            setPendingFiles((prev) => prev.filter((_, i) => i !== index))
                          }
                        >
                          <Trash2 className='size-3' />
                        </Button>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setUploadOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={pendingFiles.length === 0 || uploadMutation.isPending}
              onClick={() => uploadMutation.mutate(pendingFiles)}
            >
              {uploadMutation.isPending ? 'Uploading...' : `Upload ${pendingFiles.length || ''}`.trim()}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={Boolean(dropTarget)}
        onOpenChange={(open) => {
          if (!open) setDropTarget(null)
        }}
        title={`Delete "${dropTarget?.name ?? ''}"?`}
        desc='This removes the file from the stage storage. It cannot be undone.'
        destructive
        confirmText='Delete file'
        isLoading={deleteMutation.isPending}
        handleConfirm={() => {
          if (dropTarget) deleteMutation.mutate(dropTarget)
        }}
      />
    </div>
  )
}
