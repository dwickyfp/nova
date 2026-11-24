import { ImageOff } from 'lucide-react'
import type { ImageFill } from '../contracts'
import { useDashboardImage } from './images'

/**
 * A dashboard picture in its box. While it loads the box shows a quiet
 * placeholder; a picture that cannot be shown (removed access, a damaged
 * copy) says so in words instead of leaving an empty frame.
 */
export function DashboardPicture({ dashboardId, assetId, alt, fit }: {
  dashboardId: string; assetId: string; alt: string; fit: 'contain' | 'cover'
}) {
  const image = useDashboardImage(dashboardId, assetId)
  if (image.status === 'ready') {
    return <img src={image.url} alt={alt} draggable={false} data-testid='dashboard-image'
      className='pointer-events-none size-full select-none' style={{ objectFit: fit }} />
  }
  if (image.status === 'failed') {
    return <div role='img' aria-label={alt ? `${alt} (image unavailable)` : 'Image unavailable'}
      className='flex size-full flex-col items-center justify-center gap-1 bg-muted/60 p-2 text-center text-xs text-muted-foreground'>
      <ImageOff className='size-4' aria-hidden />
      <span>Image unavailable</span>
    </div>
  }
  return <div role='img' aria-label={alt || 'Image'} aria-busy='true' className='size-full animate-pulse bg-muted/60 motion-reduce:animate-none' />
}

/**
 * A page's background photo, behind every item. The dim layer darkens it so
 * text on top stays readable; it never takes the pointer.
 */
export function PageBackgroundImage({ dashboardId, fill }: { dashboardId: string; fill: ImageFill }) {
  const image = useDashboardImage(dashboardId, fill.asset_id)
  return <div aria-hidden data-testid='page-background-image' className='pointer-events-none absolute inset-0 overflow-hidden'>
    {image.status === 'ready' && <img src={image.url} alt='' draggable={false} className='size-full'
      style={{ objectFit: fill.fit ?? 'cover' }} />}
    {(fill.dim ?? 0) > 0 && <div className='absolute inset-0' style={{ background: `rgb(0 0 0 / ${fill.dim}%)` }} />}
  </div>
}
