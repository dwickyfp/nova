import { useQuery } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { datavizApi, locatorKey, type DatavizCapability, type DatavizLocator } from "./api";

const BATCH_MS = 30;
const MAX_BATCH = 50;

type Pending = {
  locator: DatavizLocator;
  resolve: (value: DatavizCapability | null) => void;
};

const queue: Pending[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;

function flush() {
  timer = null;
  const batch = queue.splice(0, MAX_BATCH);
  if (queue.length) timer = setTimeout(flush, 0);
  if (!batch.length) return;
  datavizApi.check(batch.map((item) => item.locator)).then(
    // A missing or malformed answer is unknown, and unknown never shows an action.
    (body) => batch.forEach((item, index) => item.resolve(body?.results?.[index] ?? null)),
    // An unavailable check never shows an action: unknown is not supported.
    () => batch.forEach((item) => item.resolve(null)),
  );
}

/** One server capability answer; results rendered together share one request. */
function load(locator: DatavizLocator): Promise<DatavizCapability | null> {
  return new Promise((resolve) => {
    queue.push({ locator, resolve });
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
    queryFn: () => load(locator as DatavizLocator),
    enabled: locator !== null,
    staleTime: 5 * 60_000,
    gcTime: 10 * 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  });
  return query.data ?? null;
}
