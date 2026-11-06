import { useState } from 'react'
import { toast } from 'sonner'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { deleteCatalog, type CatalogDetail } from './api'

export function DropCatalogDialog({
  catalog,
  onOpenChange,
  onDropped,
}: {
  catalog: CatalogDetail | { name: string } | null
  onOpenChange: (open: boolean) => void
  onDropped?: (name: string) => void
}) {
  const [dropping, setDropping] = useState(false)

  const handleDrop = async () => {
    if (!catalog) return
    setDropping(true)
    try {
      await deleteCatalog(catalog.name)
      toast.success(`Catalog "${catalog.name}" dropped`)
      onDropped?.(catalog.name)
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to drop catalog')
    } finally {
      setDropping(false)
    }
  }

  return (
    <ConfirmDialog
      open={catalog !== null}
      onOpenChange={onOpenChange}
      title='Drop external catalog'
      desc={
        <span>
          Drop <span className='font-semibold'>{catalog?.name}</span>? Tables in
          this catalog become unreachable through Nova. The underlying data is
          not deleted.
        </span>
      }
      destructive
      isLoading={dropping}
      confirmText='Drop'
      handleConfirm={handleDrop}
    />
  )
}
