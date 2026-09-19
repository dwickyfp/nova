import { useEffect, useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { api } from '@/lib/api-client'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

type ProxyInfo = {
  enabled: boolean
  host: string
  port: number
}

type ConnectionInfoDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ConnectionInfoDialog({
  open,
  onOpenChange,
}: ConnectionInfoDialogProps) {
  const [info, setInfo] = useState<ProxyInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setError(null)
    api
      .get<{ proxy: ProxyInfo }>('/system/info')
      .then((res) => {
        if (!cancelled) setInfo(res.proxy)
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const host = info?.host ?? ''
  const port = info?.port ?? ''
  const example =
    info && info.host
      ? `mysql -h ${info.host} -P ${info.port} -u <your_username> -p`
      : ''

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-md'>
        <DialogHeader>
          <DialogTitle>Connect to Nova</DialogTitle>
          <DialogDescription>
            Use any MySQL-compatible client to run Nova SQL, including{' '}
            <code className='rounded bg-muted px-1 py-0.5 text-xs'>@stage</code>{' '}
            queries.
          </DialogDescription>
        </DialogHeader>

        {error ? (
          <p className='text-sm text-destructive'>{error}</p>
        ) : (
          <div className='grid gap-3 text-sm'>
            <div className='grid gap-1'>
              <span className='text-xs font-medium uppercase tracking-wider text-muted-foreground'>
                Host
              </span>
              <CopyRow value={host} />
            </div>
            <div className='grid gap-1'>
              <span className='text-xs font-medium uppercase tracking-wider text-muted-foreground'>
                Port
              </span>
              <CopyRow value={port === '' ? '' : String(port)} />
            </div>
            <div className='grid gap-1'>
              <span className='text-xs font-medium uppercase tracking-wider text-muted-foreground'>
                Username &amp; password
              </span>
              <p className='text-muted-foreground'>
                Use your Nova account. Credentials are the same as your StarRocks
                user.
              </p>
            </div>
            {example && (
              <div className='grid gap-1'>
                <span className='text-xs font-medium uppercase tracking-wider text-muted-foreground'>
                  Example
                </span>
                <CopyRow value={example} mono />
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function CopyRow({ value, mono }: { value: string; mono?: boolean }) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    if (!value) return
    await navigator.clipboard.writeText(value)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className='flex items-center gap-2'>
      <Input
        readOnly
        value={value}
        className={mono ? 'font-mono text-xs' : 'font-mono'}
      />
      <Button
        type='button'
        size='icon'
        variant='outline'
        onClick={() => void handleCopy()}
        disabled={!value}
        aria-label='Copy'
      >
        {copied ? <Check className='size-4' /> : <Copy className='size-4' />}
      </Button>
    </div>
  )
}
