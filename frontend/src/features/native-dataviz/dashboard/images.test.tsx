import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { page as browser, userEvent } from 'vitest/browser'
import { cleanup, render } from 'vitest-browser-react'
import { useAuthStore } from '@/stores/auth-store'
import { addImageCommand, forgetImages, imageProblem, imageSize, uploadImage } from './images'
import { ImageDialog } from './insert-menu'
import { LEGACY_GRID, type Element, type Page } from './layout'
import { DashboardPicture, PageBackgroundImage } from './picture'
import '@/styles/index.css'

// Dashboard pictures: the client checks a file before sending it, sends the bytes
// as they are, shows a viewer's copy fetched with their session, and says so
// when a picture cannot be shown. The server is stubbed; its checks have their own tests.

const BOARD = '0b6f6a0e-4f5d-4c43-8c2a-2f0d5c7f1a11'
const ASSET = '7d0f0c3e-1f53-4a52-9b8e-3c1d2a4b5c6d'
const PNG = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])
const reply = { asset_id: ASSET, media_type: 'image/png', size_bytes: 8, width: 640, height: 480, created_at: '2026-11-24T00:00:00Z' }

const page = (elements: Element[] = [], size = { width: 1280, height: 720 }): Page => ({
  page_id: 'p', title: 'P', elements, tab_order: [], mobile: { order: [], hidden: [] }, legacy_spacers: [],
  canvas: { size: 'custom', ...size, fit: 'fixed', background: null, grid: { ...LEGACY_GRID, show: true, snap: true, snap_to_elements: true } },
} as unknown as Page)

/** A real one-pixel PNG, so the browser can draw it. */
async function onePixel(): Promise<Blob> {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  return await new Promise(resolve => canvas.toBlob(blob => resolve(blob!), 'image/png'))
}

let fetches: { url: string; init: RequestInit }[] = []
beforeEach(() => { fetches = []; forgetImages() })
afterEach(async () => { await cleanup(); vi.unstubAllGlobals() })

describe('dashboard images', () => {
  it('checks the type and size before anything is sent', () => {
    expect(imageProblem(new File([PNG], 'a.png', { type: 'image/png' }))).toBeNull()
    expect(imageProblem(new File(['<svg/>'], 'a.svg', { type: 'image/svg+xml' }))).toContain('SVG')
    expect(imageProblem(new File([PNG], 'a.gif', { type: 'image/gif' }))).toContain('PNG, JPEG or WebP')
    expect(imageProblem(new File([new Uint8Array(2 * 1024 * 1024 + 1)], 'big.png', { type: 'image/png' }))).toContain('up to 2 MB')
  })

  it('sends the picture as the request body with its own type', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      fetches.push({ url, init })
      return new Response(JSON.stringify(reply), { status: 201, headers: { 'content-type': 'application/json' } })
    }))
    const file = new File([PNG], 'warehouse.png', { type: 'image/png' })
    const saved = await uploadImage(BOARD, file, useAuthStore.getState().securityEpoch)
    expect(saved).toMatchObject({ asset_id: ASSET, width: 640, height: 480 })
    expect(fetches[0].url).toMatch(new RegExp(`/viz/dashboards/${BOARD}/images$`))
    expect(fetches[0].init.method).toBe('POST')
    expect(fetches[0].init.body).toBe(file)
    expect((fetches[0].init.headers as Record<string, string>)['Content-Type']).toBe('image/png')
  })

  it('places a new image in its own proportions, inside the page', () => {
    expect(imageSize({ width: 640, height: 480 }, page())).toEqual({ w: 320, h: 240 })
    expect(imageSize({ width: 100, height: 400 }, page())).toEqual({ w: 60, h: 240 })
    expect(imageSize({ width: 40, height: 30 }, page())).toEqual({ w: 40, h: 30 })  // never enlarged
    const command = addImageCommand(page(), { ...reply, media_type: 'image/png' }, 'Warehouse at night', 'e1')
    expect(command.changes![0]).toMatchObject({ op: 'elements.add', elements: [{
      kind: 'image', element_id: 'e1', name: 'Image 1', asset_id: ASSET, alt: 'Warehouse at night', fit: 'contain', decorative: false,
      geometry: { x: LEGACY_GRID.margin, y: LEGACY_GRID.margin, w: 320, h: 240 } }] })  // the first free spot, at the margin
    expect(command).toMatchObject({ select: ['e1'], message: `Added an image at x ${LEGACY_GRID.margin}, y ${LEGACY_GRID.margin}` })
  })

  it('shows a viewer\'s copy fetched with their session, once per role', async () => {
    const blob = await onePixel()
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      fetches.push({ url, init })
      return new Response(blob, { status: 200, headers: { 'content-type': 'image/png' } })
    }))
    const screen = await render(<div style={{ width: 200, height: 100 }}>
      <DashboardPicture dashboardId={BOARD} assetId={ASSET} alt='Warehouse at night' fit='cover' />
      <DashboardPicture dashboardId={BOARD} assetId={ASSET} alt='Warehouse at night' fit='cover' />
    </div>)
    await expect.element(screen.getByTestId('dashboard-image').first()).toBeVisible()
    await expect.element(screen.getByRole('img', { name: 'Warehouse at night' }).first()).toBeVisible()
    const shown = screen.getByTestId('dashboard-image').first().element() as HTMLImageElement
    expect(shown.src).toMatch(/^blob:/)
    expect(shown.style.objectFit).toBe('cover')
    expect(fetches).toHaveLength(1)
    expect(fetches[0].url).toMatch(new RegExp(`/viz/dashboards/${BOARD}/images/${ASSET}$`))
    expect(fetches[0].init.cache).toBe('no-store')
    // A role change fetches again: the copy belongs to the role that saw it.
    await cleanup()
    useAuthStore.setState(state => ({ securityEpoch: state.securityEpoch + 1 }))
    await render(<DashboardPicture dashboardId={BOARD} assetId={ASSET} alt='Warehouse at night' fit='cover' />)
    await expect.poll(() => fetches.length).toBe(2)
  })

  it('says a picture is unavailable instead of leaving an empty box', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Image not found' }), {
      status: 404, headers: { 'content-type': 'application/json' } })))
    const screen = await render(<DashboardPicture dashboardId={BOARD} assetId={ASSET} alt='Warehouse at night' fit='contain' />)
    await expect.element(screen.getByRole('img', { name: 'Warehouse at night (image unavailable)' })).toBeVisible()
    await expect.element(screen.getByText('Image unavailable')).toBeVisible()
  })

  it('darkens a background photo and keeps it out of the way', async () => {
    const blob = await onePixel()
    vi.stubGlobal('fetch', vi.fn(async () => new Response(blob, { status: 200, headers: { 'content-type': 'image/png' } })))
    const screen = await render(<div style={{ position: 'relative', width: 300, height: 200 }}>
      <PageBackgroundImage dashboardId={BOARD} fill={{ kind: 'image', asset_id: ASSET, fit: 'cover', dim: 40 }} />
    </div>)
    const layer = screen.getByTestId('page-background-image')
    await expect.poll(() => layer.element().querySelector('img')?.getAttribute('src') ?? '').toMatch(/^blob:/)
    expect(getComputedStyle(layer.element()).pointerEvents).toBe('none')
    expect(layer.element().getAttribute('aria-hidden')).toBe('true')
    expect((layer.element().lastElementChild as HTMLElement).style.background).toContain('0.4')
  })
})

describe('Add an image dialog', () => {
  const pick = async (file: File) => {
    const input = document.querySelector<HTMLInputElement>('#image-file')!
    await userEvent.upload(browser.elementLocator(input), file)
  }

  it('needs a picture and a description, then adds it', async () => {
    const upload = vi.fn(async () => ({ ...reply, media_type: 'image/png' as const }))
    const onAdd = vi.fn()
    const onOpenChange = vi.fn()
    const screen = await render(<ImageDialog open onOpenChange={onOpenChange} upload={upload} onAdd={onAdd} />)
    const add = screen.getByRole('button', { name: 'Add image' })
    await expect.element(add).toBeDisabled()
    await pick(new File(['<svg/>'], 'logo.svg', { type: 'image/svg+xml' }))
    await expect.element(screen.getByRole('alert')).toHaveTextContent('including SVG, are not accepted')
    await pick(new File([PNG], 'warehouse.png', { type: 'image/png' }))
    await expect.element(screen.getByRole('alert')).not.toBeInTheDocument()
    await expect.element(add).toBeDisabled()
    await screen.getByLabelText('Description for screen readers').fill('  Jakarta warehouse at night ')
    await add.click()
    await expect.poll(() => onAdd.mock.calls.length).toBe(1)
    expect(upload).toHaveBeenCalledTimes(1)
    expect(onAdd.mock.calls[0][1]).toBe('Jakarta warehouse at night')
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('keeps the dialog open with the server\'s reason when the upload is refused', async () => {
    const upload = vi.fn(async () => { throw new Error('This PNG picture is damaged') })
    const onAdd = vi.fn()
    const screen = await render(<ImageDialog open onOpenChange={() => undefined} upload={upload} onAdd={onAdd} />)
    await pick(new File([PNG], 'broken.png', { type: 'image/png' }))
    await screen.getByLabelText('Description for screen readers').fill('Broken')
    await screen.getByRole('button', { name: 'Add image' }).click()
    await expect.element(screen.getByRole('alert')).toHaveTextContent('This PNG picture is damaged')
    expect(onAdd).not.toHaveBeenCalled()
    await expect.element(screen.getByRole('button', { name: 'Add image' })).toBeEnabled()
  })
})
