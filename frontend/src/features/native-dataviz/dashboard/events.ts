/** Window event raised when Neva commits a change to a native dashboard. */
export const NATIVE_DASHBOARD_CHANGED = 'nova:native-dashboard-changed'

export type NativeDashboardChanged = { dashboard_id: string; revision: number }

/** Window event: a change Neva proposes on a native dashboard, and how it went. */
export const NATIVE_DASHBOARD_ACTIVITY = 'nova:native-dashboard-activity'

export type NevaAction = 'add' | 'change' | 'move' | 'remove' | 'arrange' | 'filter' | 'data' | 'publish'
/** waiting: asks for approval; saving: approved and running; saved, failed and stopped are final. */
export type NevaState = 'waiting' | 'saving' | 'saved' | 'failed' | 'stopped'
export type NativeDashboardActivity =
  | { kind: 'proposed'; tool_call_id: string; dashboard_id: string; action: NevaAction; widget_ids: string[]; page_id?: string; state: 'waiting' | 'saving' }
  | { kind: 'status'; tool_call_id: string; state: NevaState }

/** Window event that asks the Dataviz tab to open one native dashboard. */
export const NATIVE_DASHBOARD_OPEN = 'nova:native-dashboard-open'

import type { CrossFilter } from './filter-model'

/** A value carried into the opened dashboard, applied like a chart selection. */
export type LinkedSelection = Omit<CrossFilter, 'source_widget_id'>

export type NativeDashboardOpen = { dashboardId: string; workspaceId: string; selections?: LinkedSelection[] }
