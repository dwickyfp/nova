import {
  Fragment,
  useCallback,
  useRef,
  useState,
} from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertCircle,
  Box,
  ChevronRight,
  FileText,
  FolderOpen,
  Loader2,
  RefreshCw,
  Trash2,
  Upload,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { cn } from '@/lib/utils'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
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
import { Label } from '@/components/ui/label'
import { formatBytes } from './helpers'
import type { ExplorerNode } from './types'

// ── Stage Files Panel ─────────────────────────────────────────

type StageFile = {
  name: string
  size: number
  last_modified: string | null
  is_dir: boolean
}

export function StageFilesPanel({ node }: { node: ExplorerNode }) {
  const queryClient = useQueryClient()
  const stageName = node.label.replace(/^@/, '')
  const database = node.database || ''

  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [uploadFolder, setUploadFolder] = useState('')
  const [currentPath, setCurrentPath] = useState<string[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const pathPrefix = currentPath.join('/')

  // Fetch files with current path prefix
  const {
    data: filesData,
    isLoading: filesLoading,
    isFetching: filesFetching,
    error: filesError,
    refetch: refetchFiles,
  } = useQuery<{ files: StageFile[]; count: number }>({
    queryKey: ['explorer-stage-files', database, stageName, pathPrefix],
    queryFn: () =>
      api.get(
        `/explorer/databases/${database}/stages/${stageName}/files${pathPrefix ? `?prefix=${encodeURIComponent(pathPrefix)}` : ''}`,
      ),
    enabled: !!database && !!stageName,
  })

  const handleRefreshFiles = useCallback(async () => {
    setRefreshing(true)
    await refetchFiles()
    setRefreshing(false)
  }, [refetchFiles])

  const files = filesData?.files ?? []

  // Upload handler
  const handleUpload = useCallback(async (filesToUpload: File[]) => {
    if (!filesToUpload.length) return
    setUploading(true)
    try {
      const extraFolder = uploadFolder.trim() ? `${uploadFolder.trim().replace(/^\//, '')}/` : ''
      const basePath = currentPath.length > 0 ? `${currentPath.join('/')}/` : ''
      const fullPrefix = `${basePath}${extraFolder}`
      for (const file of filesToUpload) {
        const formData = new FormData()
        formData.append('file', file)
        formData.append('filename', `${fullPrefix}${file.name}`)
        await api.upload(`/explorer/databases/${database}/stages/${stageName}/files`, formData)
      }
      const destLabel = fullPrefix ? ` to /${fullPrefix.replace(/\/$/, '')}` : ''
      toast.success(`${filesToUpload.length} file(s) uploaded${destLabel}`)
      setUploadOpen(false)
      setPendingFiles([])
      setUploadFolder('')
      queryClient.invalidateQueries({ queryKey: ['explorer-stage-files', database, stageName] })
    } catch (err) {
      toast.error(`Upload failed: ${String(err)}`)
    } finally {
      setUploading(false)
    }
  }, [database, stageName, queryClient, uploadFolder, currentPath])

  // Delete handler
  const handleDelete = useCallback(async (filename: string) => {
    try {
      const targetName = currentPath.length > 0 ? `${currentPath.join('/')}/${filename}` : filename
      await api.delete(`/explorer/databases/${database}/stages/${stageName}/files/${targetName}`)
      toast.success(`Deleted "${filename}"`)
      queryClient.invalidateQueries({ queryKey: ['explorer-stage-files', database, stageName] })
    } catch (err) {
      toast.error(`Delete failed: ${String(err)}`)
    }
    setDeleteTarget(null)
  }, [database, stageName, queryClient, currentPath])

  // Drop handlers
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const droppedFiles = Array.from(e.dataTransfer.files)
    if (droppedFiles.length) {
      setPendingFiles((prev) => [...prev, ...droppedFiles])
    }
  }, [])

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files || [])
    if (selected.length) {
      setPendingFiles((prev) => [...prev, ...selected])
    }
  }, [])

  return (
    <div className='space-y-5'>
      {/* Header */}
      <div className='flex flex-wrap items-center gap-3'>
        <div className='flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary'>
          <Box className='h-5 w-5' />
        </div>
        <div className='flex-1 space-y-1'>
          <div className='flex flex-wrap items-center gap-2'>
            <h1 className='text-3xl font-semibold tracking-tight'>{stageName}</h1>
            <Badge variant='secondary'>Stage</Badge>
          </div>
          <p className='text-sm text-muted-foreground'>{node.path.join(' / ')}</p>
        </div>
        <Button size='sm' onClick={() => { setPendingFiles([]); setUploadFolder(''); setUploadOpen(true) }}>
          <Upload className='mr-2 h-4 w-4' />
          Upload
        </Button>
      </div>

      {/* Tab */}
      <div className='border-b border-border'>
        <div className='inline-flex border-b-2 border-primary px-1 py-3 text-sm font-medium text-primary dark:text-foreground'>
          Stage Files
        </div>
      </div>

      {/* Breadcrumb navigation */}
      {currentPath.length > 0 && (
        <div className='flex items-center gap-1 text-sm'>
          <button
            type='button'
            onClick={() => setCurrentPath([])}
            className='text-muted-foreground hover:text-foreground transition-colors'
          >
            {stageName}
          </button>
          {currentPath.map((seg, i) => (
            <Fragment key={i}>
              <ChevronRight className='h-3.5 w-3.5 text-muted-foreground/60' />
              {i === currentPath.length - 1 ? (
                <span className='font-medium'>{seg}</span>
              ) : (
                <button
                  type='button'
                  onClick={() => setCurrentPath(currentPath.slice(0, i + 1))}
                  className='text-muted-foreground hover:text-foreground transition-colors'
                >
                  {seg}
                </button>
              )}
            </Fragment>
          ))}
        </div>
      )}

      {/* Files list */}
      <section>
        {filesLoading ? (
          <div className='flex items-center gap-2 p-6 text-sm text-muted-foreground'>
            <Loader2 className='h-4 w-4 animate-spin' />
            Loading files...
          </div>
        ) : filesError ? (
          <div className='flex items-center gap-2 p-6 text-sm text-destructive'>
            <AlertCircle className='h-4 w-4' />
            Failed to load files: {String(filesError)}
          </div>
        ) : files.length === 0 ? (
          <div className='flex flex-col items-center gap-2 py-12 text-center'>
            <Box className='h-10 w-10 text-muted-foreground/40' />
            <p className='text-sm font-medium'>
              {currentPath.length > 0 ? 'This folder is empty' : 'No files yet'}
            </p>
            <p className='text-xs text-muted-foreground'>
              {currentPath.length === 0 && (
                <>Upload files or use <code className='rounded bg-muted px-1 py-0.5 text-xs'>SELECT * FROM @{stageName}.your_file.csv</code> to query.</>
              )}
            </p>
          </div>
        ) : (
          <>
          <div className='flex items-center justify-between px-4 py-2'>
            <div className='flex items-center gap-2'>
              <span className='text-xs text-muted-foreground'>
                {files.filter((f: StageFile) => !f.is_dir).length} file{files.filter((f: StageFile) => !f.is_dir).length !== 1 ? 's' : ''}
                {files.filter((f: StageFile) => f.is_dir).length > 0 && `, ${files.filter((f: StageFile) => f.is_dir).length} folder${files.filter((f: StageFile) => f.is_dir).length !== 1 ? 's' : ''}`}
              </span>
              <span className='text-xs text-muted-foreground'>·</span>
              <span className='text-xs text-muted-foreground'>
                {formatBytes(files.filter((f: StageFile) => !f.is_dir).reduce((sum: number, f: StageFile) => sum + f.size, 0))} total
              </span>
            </div>
            <button
              type='button'
              onClick={handleRefreshFiles}
              disabled={refreshing || filesFetching}
              className='rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50'
              aria-label='Refresh files'
            >
              <RefreshCw className={cn('h-3.5 w-3.5 transition-transform', (refreshing || filesFetching) && 'animate-spin')} />
            </button>
          </div>
          <table className='w-full'>
            <thead>
              <tr className='border-b border-border text-left text-xs font-medium uppercase tracking-wider text-muted-foreground'>
                <th className='px-4 py-3'>Name</th>
                <th className='w-28 px-4 py-3 text-right'>Size</th>
                <th className='w-52 px-4 py-3 text-right'>Last Modified</th>
                <th className='w-12 px-4 py-3' />
              </tr>
            </thead>
            <tbody className='divide-y divide-border'>
              {/* Back row when inside a folder */}
              {currentPath.length > 0 && (
                <tr
                  className='cursor-pointer hover:bg-muted/30'
                  onClick={() => setCurrentPath(currentPath.slice(0, -1))}
                >
                  <td className='px-4 py-3'>
                    <div className='flex items-center gap-2'>
                      <ChevronRight className='h-4 w-4 rotate-180 text-muted-foreground' />
                      <span className='text-sm text-muted-foreground'>..</span>
                    </div>
                  </td>
                  <td /><td /><td />
                </tr>
              )}
              {files.map((file: StageFile) => (
                <tr
                  key={file.name}
                  className={cn('hover:bg-muted/30', file.is_dir && 'cursor-pointer')}
                  onClick={file.is_dir ? () => setCurrentPath([...currentPath, file.name]) : undefined}
                >
                  <td className='px-4 py-3'>
                    <div className='flex min-w-0 items-center gap-2'>
                      {file.is_dir ? (
                        <FolderOpen className='h-4 w-4 shrink-0 text-muted-foreground' />
                      ) : (
                        <FileText className='h-4 w-4 shrink-0 text-muted-foreground' />
                      )}
                      <span className='truncate text-sm font-medium'>{file.name}</span>
                    </div>
                  </td>
                  <td className='px-4 py-3 text-right text-sm text-muted-foreground'>
                    {file.is_dir ? '—' : formatBytes(file.size)}
                  </td>
                  <td className='px-4 py-3 text-right text-sm text-muted-foreground'>
                    {file.last_modified
                      ? new Date(file.last_modified).toLocaleDateString('en-US', {
                          year: 'numeric', month: 'short', day: 'numeric',
                          hour: '2-digit', minute: '2-digit',
                        })
                      : '—'}
                  </td>
                  <td className='px-4 py-3 text-right'>
                    {!file.is_dir && (
                      <button
                        type='button'
                        onClick={(e) => { e.stopPropagation(); setDeleteTarget(file.name) }}
                        className='rounded p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive'
                      >
                        <Trash2 className='h-3.5 w-3.5' />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </>
        )}
      </section>

      {/* Upload Dialog */}
      <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
        <DialogContent className='sm:max-w-lg'>
          <DialogHeader>
            <DialogTitle>Upload to @{stageName}</DialogTitle>
            <DialogDescription>
              Drag and drop files or browse to upload to this stage.
            </DialogDescription>
          </DialogHeader>

          <div className='space-y-4 py-2'>
            {/* Drop zone */}
            <div
              onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={cn(
                'flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 transition-colors',
                dragOver
                  ? 'border-primary bg-primary/5'
                  : 'border-border hover:border-muted-foreground/50',
              )}
            >
              <Upload className={cn('mb-3 h-8 w-8', dragOver ? 'text-primary' : 'text-muted-foreground/60')} />
              <p className='text-sm font-medium'>Drop files here or click to browse</p>
              <p className='mt-1 text-xs text-muted-foreground'>CSV, JSON, Parquet, ORC, Avro</p>
              <input
                ref={fileInputRef}
                type='file'
                multiple
                className='hidden'
                onChange={handleFileInput}
              />
            </div>

            {/* Folder name input */}
            <div className='space-y-1.5'>
              <Label htmlFor='upload-folder' className='text-sm'>
                Folder <span className='font-normal text-muted-foreground'>(optional)</span>
              </Label>
              <div className='flex items-center rounded-md border border-border'>
                <input
                  id='upload-folder'
                  type='text'
                  placeholder='/e.g. data/import'
                  value={uploadFolder}
                  onChange={(e) => {
                    let val = e.target.value
                    if (val && !val.startsWith('/')) {
                      val = '/' + val
                    }
                    setUploadFolder(val)
                  }}
                  onKeyDown={(e) => {
                    if (!uploadFolder && e.key.length === 1 && e.key !== '/') {
                      setUploadFolder('/' + e.key)
                      e.preventDefault()
                    }
                  }}
                  className='flex-1 bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60'
                />
              </div>
              {uploadFolder && (
                <p className='text-xs text-muted-foreground'>
                  Files will be uploaded to <code className='rounded bg-muted px-1 py-0.5 text-xs'>{uploadFolder.replace(/^\//, '')}/</code>
                </p>
              )}
            </div>

            {/* Pending files list */}
            {pendingFiles.length > 0 && (
              <div className='space-y-2'>
                <p className='text-sm font-medium'>Files to upload ({pendingFiles.length})</p>
                <div className='max-h-40 space-y-1 overflow-y-auto rounded-lg border border-border p-2'>
                  {pendingFiles.map((f, i) => (
                    <div key={i} className='flex items-center justify-between rounded px-2 py-1 text-sm hover:bg-muted/50'>
                      <div className='flex min-w-0 items-center gap-2'>
                        <FileText className='h-3.5 w-3.5 shrink-0 text-muted-foreground' />
                        <span className='truncate'>{f.name}</span>
                      </div>
                      <div className='flex items-center gap-2'>
                        <span className='text-xs text-muted-foreground'>{formatBytes(f.size)}</span>
                        <button
                          type='button'
                          onClick={() => setPendingFiles((prev) => prev.filter((_, idx) => idx !== i))}
                          className='rounded p-0.5 text-muted-foreground hover:text-destructive'
                        >
                          <Trash2 className='h-3 w-3' />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant='outline' onClick={() => { setUploadOpen(false); setPendingFiles([]); setUploadFolder('') }}>
              Cancel
            </Button>
            <Button
              onClick={() => handleUpload(pendingFiles)}
              disabled={pendingFiles.length === 0 || uploading}
            >
              {uploading ? (
                <>
                  <Loader2 className='mr-2 h-4 w-4 animate-spin' />
                  Uploading...
                </>
              ) : (
                <>
                  <Upload className='mr-2 h-4 w-4' />
                  Upload {pendingFiles.length > 0 ? `(${pendingFiles.length})` : ''}
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete File</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete <strong>{deleteTarget}</strong>? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setDeleteTarget(null)}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteTarget && handleDelete(deleteTarget)}
              className='bg-destructive text-destructive-foreground hover:bg-destructive/90'
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
