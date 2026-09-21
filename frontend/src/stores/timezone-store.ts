import { create } from "zustand";

/** Fallback when the backend has not answered yet. Matches NOVA_TIMEZONE. */
export const DEFAULT_TIMEZONE = "Asia/Jakarta";

interface TimezoneState {
  timezone: string;
  setTimezone: (timezone: string) => void;
}

/**
 * The deployment timezone, sourced from `GET /system/info`.
 *
 * Timestamps are rendered in this zone rather than the browser's, so every
 * user reads the same clock and the numbers match what StarRocks writes with
 * `NOW()`. The store lives outside React so non-component code (utilities,
 * export helpers) can format dates without a hook.
 */
export const useTimezoneStore = create<TimezoneState>()((set) => ({
  timezone: DEFAULT_TIMEZONE,
  setTimezone: (timezone) =>
    set({ timezone: (timezone || "").trim() || DEFAULT_TIMEZONE }),
}));

export function getTimezone(): string {
  return useTimezoneStore.getState().timezone;
}
