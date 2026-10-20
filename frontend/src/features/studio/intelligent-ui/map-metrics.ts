import { useCallback, useContext, useRef } from "react";
import { uiApi } from "./api";
import { UiRuntimeContext } from "./runtime-context";

export function useMapMetrics() {
  const { source } = useContext(UiRuntimeContext);
  const started = useRef(performance.now());
  const sent = useRef(new Set<string>());
  return useCallback(
    (event: "ready" | "failed" | "fallback" | "route_unavailable") => {
      if (!source || sent.current.has(event)) return;
      sent.current.add(event);
      void uiApi
        .mapMetric(
          source,
          event,
          Math.min(300_000, performance.now() - started.current),
        )
        .catch(() => {});
    },
    [source],
  );
}
