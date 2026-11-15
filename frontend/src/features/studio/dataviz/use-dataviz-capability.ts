import { useQuery } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { datavizApi, locatorKey, type DatavizCapability, type DatavizLocator } from "./api";

const BATCH_MS = 30;
const MAX_BATCH = 50;

type Pending = {
  locator: DatavizLocator;
  securityEpoch: number;
  settled: boolean;
  resolve: (value: DatavizCapability | null) => void;
  dispose: () => void;
  cancelBatch?: () => void;
};

const queue: Pending[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;

function finish(item: Pending, value: DatavizCapability | null) {
  if (item.settled) return;
  item.settled = true;
  item.dispose();
  item.resolve(value);
}

function flush() {
  timer = null;
  const epoch = useAuthStore.getState().securityEpoch;
  const batch = queue.splice(0, MAX_BATCH).filter((item) => {
    if (item.securityEpoch === epoch) return true;
    finish(item, null);
    return false;
  });
  if (queue.length) timer = setTimeout(flush, 0);
  if (!batch.length) return;
  const controller = new AbortController();
  for (const item of batch) {
    item.cancelBatch = () => {
      if (batch.every((pending) => pending.settled)) controller.abort();
    };
  }
  datavizApi.check(batch.map((item) => item.locator), controller.signal).then(
    // A missing or malformed answer is unknown, and unknown never shows an action.
    (body) => batch.forEach((item, index) => finish(item,
      useAuthStore.getState().securityEpoch === epoch ? body?.results?.[index] ?? null : null,
    )),
    // An unavailable check never shows an action: unknown is not supported.
    () => batch.forEach((item) => finish(item, null)),
  );
}

/** One server capability answer; results rendered together share one request. */
function load(
  locator: DatavizLocator, signal: AbortSignal, securityEpoch: number,
): Promise<DatavizCapability | null> {
  if (signal.aborted || useAuthStore.getState().securityEpoch !== securityEpoch)
    return Promise.resolve(null);
  return new Promise((resolve) => {
    const item: Pending = {
      locator, securityEpoch, settled: false, resolve,
      dispose: () => signal.removeEventListener("abort", cancel),
    };
    function cancel() {
      const index = queue.indexOf(item);
      if (index !== -1) queue.splice(index, 1);
      finish(item, null);
      item.cancelBatch?.();
      if (!queue.length && timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    }
    signal.addEventListener("abort", cancel, { once: true });
    queue.push(item);
    if (!timer) timer = setTimeout(flush, BATCH_MS);
  });
}

/**
 * Whether Nova's server resolver allows adding this result to Dataviz.
 *
 * Only the server decides; the card shows **Add to Dataviz** when ``can_add``
 * is true and nothing at all otherwise (no badge, no disabled button).
 */
export function useDatavizCapability(locator: DatavizLocator | null) {
  const securityEpoch = useAuthStore((state) => state.securityEpoch);
  const query = useQuery({
    queryKey: ["dataviz", "capability", securityEpoch, locator ? locatorKey(locator) : null],
    queryFn: ({ signal }) => load(locator as DatavizLocator, signal, securityEpoch),
    enabled: locator !== null,
    staleTime: 5 * 60_000,
    gcTime: 10 * 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  });
  return query.data ?? null;
}
