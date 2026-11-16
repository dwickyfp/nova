/** Window event raised when Neva commits a change to a native dashboard. */
export const NATIVE_DASHBOARD_CHANGED = 'nova:native-dashboard-changed'

export type NativeDashboardChanged = { dashboard_id: string; revision: number }

/** Window event that asks the Dataviz tab to open one native dashboard. */
export const NATIVE_DASHBOARD_OPEN = 'nova:native-dashboard-open'

import type { CrossFilter } from './filter-model'

/** A value carried into the opened dashboard, applied like a chart selection. */
export type LinkedSelection = Omit<CrossFilter, 'source_widget_id'>

export type NativeDashboardOpen = { dashboardId: string; workspaceId: string; selections?: LinkedSelection[] }
