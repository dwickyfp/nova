import { useCallback, useState } from 'react'
import { DashboardEditor, type NativeDashboardContext } from './dashboard/editor'
import { DashboardHome } from './dashboard/dashboard-home'

const OPEN_KEY = 'nova.dataviz.native.open'
type Open = { dashboardId: string; workspaceId: string }

function readOpen(): Open | null {
  try {
    const value = JSON.parse(localStorage.getItem(OPEN_KEY) ?? 'null') as Open | null
    return value && typeof value.dashboardId === 'string' && typeof value.workspaceId === 'string' ? value : null
  } catch {
    return null
  }
}

function writeOpen(value: Open | null) {
  try {
    if (value) localStorage.setItem(OPEN_KEY, JSON.stringify(value)); else localStorage.removeItem(OPEN_KEY)
  } catch { /* a per-browser convenience */ }
}

/** Native Dataviz: the dashboard list and the shared Canvas/Data editor. */
export function NativeDataviz({ onContextChange, onAskNeva, refreshToken, density }: {
  onContextChange?: (context: NativeDashboardContext | null) => void
  onAskNeva?: (prompt: string) => void
  refreshToken?: number
  density?: 'full' | 'panel'
}) {
  const [open, setOpen] = useState<Open | null>(readOpen)
  const select = useCallback((value: Open | null) => { setOpen(value); writeOpen(value) }, [])
  if (!open) return <DashboardHome onOpen={select} />
  return <DashboardEditor key={open.dashboardId} dashboardId={open.dashboardId} workspaceId={open.workspaceId}
    onBack={() => select(null)} onContextChange={onContextChange} onAskNeva={onAskNeva} refreshToken={refreshToken} density={density} />
}
