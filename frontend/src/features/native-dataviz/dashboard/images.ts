import { useEffect, useState } from 'react'
import { z } from 'zod'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import type { ImageFill } from '../contracts'
import type { Command } from './commands'
import { clampBox, pageSize, toGeometry } from './frame'
import { freeSpot } from './insert'
import type { Element, Page } from './layout'

/**
 * Dashboard pictures: an image item or a page's background photo. The bytes
 * live with the dashboard and are served only to people who can view it, so
 * the page fetches them with the viewer's session and shows a local copy.
 */

export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'] as const
export const MAX_IMAGE_BYTES = 2 * 1024 * 1024
export const IMAGE_ACCEPT = IMAGE_TYPES.join(',')

const uploaded = z.object({
  asset_id: z.uuid(),
  media_type: z.enum(IMAGE_TYPES),
  size_bytes: z.number().int().min(1).max(MAX_IMAGE_BYTES),
  width: z.number().int().min(1),
  height: z.number().int().min(1),
})
export type UploadedImage = z.infer<typeof uploaded>

/** Why a file cannot be used, before it is sent; the server checks the bytes again. */
export function imageProblem(file: File): string | null {
  if (!(IMAGE_TYPES as readonly string[]).includes(file.type)) {
    return 'Use a PNG, JPEG or WebP picture. Other files, including SVG, are not accepted.'
  }
  if (file.size > MAX_IMAGE_BYTES) return 'Pictures can be up to 2 MB. Make this one smaller and try again.'
  if (!file.size) return 'This file is empty.'
  return null
}

const imagesPath = (dashboardId: string) => `/viz/dashboards/${z.uuid().parse(dashboardId).toLowerCase()}/images`

export async function uploadImage(dashboardId: string, file: File, securityEpoch: number): Promise<UploadedImage> {
  const problem = imageProblem(file)
  if (problem) throw new Error(problem)
  const response = await api.request<unknown>(imagesPath(dashboardId), {
    method: 'POST', body: file, headers: { 'Content-Type': file.type }, securityEpoch, cache: 'no-store',
  })
  const result = uploaded.safeParse(response)
  if (!result.success) throw new Error('The picture was saved, but the reply was not understood. Reload and try again.')
  return result.data
}

// A picture is fetched once per signed-in role and kept for this page session;
// a role or user change clears it, so another identity never sees it.
const LIMIT = 64
const pictures = new Map<string, Promise<Blob>>()
let epochSeen: number | null = null

function picture(dashboardId: string, assetId: string, securityEpoch: number): Promise<Blob> {
  if (epochSeen !== securityEpoch) { pictures.clear(); epochSeen = securityEpoch }
  const key = `${dashboardId}/${assetId}`
  let found = pictures.get(key)
  if (!found) {
    found = api.request<Blob>(`${imagesPath(dashboardId)}/${z.uuid().parse(assetId).toLowerCase()}`, {
      as: 'blob', securityEpoch, cache: 'no-store',
    })
    found.catch(() => pictures.delete(key))
    pictures.set(key, found)
    if (pictures.size > LIMIT) pictures.delete(pictures.keys().next().value!)
  }
  return found
}

/** Forget cached pictures (tests, and sign-out through the epoch). */
export function forgetImages() {
  pictures.clear()
  epochSeen = null
}

export type ImageState = { status: 'loading' } | { status: 'ready'; url: string } | { status: 'failed' }

/** A dashboard picture as a page-local address, released when it is no longer shown. */
export function useDashboardImage(dashboardId: string | undefined, assetId: string | undefined): ImageState {
  const epoch = useAuthStore(state => state.securityEpoch)
  const [state, setState] = useState<ImageState>({ status: 'loading' })
  useEffect(() => {
    if (!dashboardId || !assetId) { setState({ status: 'failed' }); return }
    let url: string | null = null
    let live = true
    setState({ status: 'loading' })
    picture(dashboardId, assetId, epoch).then(blob => {
      if (!live) return
      url = URL.createObjectURL(blob)
      setState({ status: 'ready', url })
    }, () => { if (live) setState({ status: 'failed' }) })
    return () => {
      live = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [dashboardId, assetId, epoch])
  return state
}

/** A new image's size: up to 320 × 240 page pixels, in the picture's own proportions. */
export function imageSize(image: Pick<UploadedImage, 'width' | 'height'>, page: Page): { w: number; h: number } {
  const room = pageSize(page)
  const most = { w: Math.min(320, room.width - 48), h: Math.min(240, room.height - 48) }
  const scale = Math.min(most.w / image.width, most.h / image.height, 1)
  return { w: Math.max(24, Math.round(image.width * scale)), h: Math.max(24, Math.round(image.height * scale)) }
}

export function addImageCommand(page: Page, image: UploadedImage, alt: string, id: string): Command {
  const size = imageSize(image, page)
  const box = clampBox(freeSpot(page, size.w, size.h), page)
  const count = (page.elements ?? []).filter(item => item.kind === 'image').length
  const element = { kind: 'image', element_id: id, name: `Image ${count + 1}`, locked: false, hidden: false, decorative: false,
    geometry: toGeometry(box), asset_id: image.asset_id, alt, fit: 'contain', title: '' } as Element
  return { changes: [{ op: 'elements.add', page_id: page.page_id, elements: [element] }], select: [id],
    message: `Added an image at x ${box.x}, y ${box.y}` }
}

export function backgroundImage(image: UploadedImage, current?: ImageFill | null): ImageFill {
  return { kind: 'image', asset_id: image.asset_id, fit: current?.fit ?? 'cover', dim: current?.dim ?? 0 }
}
