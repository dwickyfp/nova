/** Window event raised when Neva commits a change to a native dashboard. */
export const NATIVE_DASHBOARD_CHANGED = 'nova:native-dashboard-changed'

export type NativeDashboardChanged = { dashboard_id: string; revision: number }
