const flushers = new Set<() => Promise<void>>();
export function registerUiFlush(flush: () => Promise<void>) {
  flushers.add(flush);
  return () => {
    flushers.delete(flush);
  };
}
export async function flushUiStates() {
  await Promise.all([...flushers].map((flush) => flush()));
}
