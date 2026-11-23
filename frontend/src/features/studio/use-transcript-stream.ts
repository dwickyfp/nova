import { useCallback, useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import { applyStudioEvent, type StudioEvent, type TranscriptTurn } from "./studio-transcript";

export function useTranscriptStream(setTurns: Dispatch<SetStateAction<TranscriptTurn[]>>) {
  const abortRef = useRef<AbortController | null>(null);
  const queuedEventsRef = useRef<Array<{ turnId: string; event: StudioEvent }>>([]);
  const eventFrameRef = useRef<number | null>(null);

  const flushEvents = useCallback(() => {
    if (eventFrameRef.current !== null) {
      window.cancelAnimationFrame(eventFrameRef.current);
      eventFrameRef.current = null;
    }
    const queued = queuedEventsRef.current.splice(0);
    if (!queued.length) return;
    setTurns((current) => queued.reduce(
      (next, item) => applyStudioEvent(next, item.turnId, item.event), current,
    ));
  }, [setTurns]);

  const queueEvent = useCallback((turnId: string, event: StudioEvent) => {
    queuedEventsRef.current.push({ turnId, event });
    if (eventFrameRef.current !== null) return;
    eventFrameRef.current = window.requestAnimationFrame(() => {
      eventFrameRef.current = null;
      flushEvents();
    });
  }, [flushEvents]);

  const discardQueuedEvents = useCallback(() => {
    if (eventFrameRef.current !== null) {
      window.cancelAnimationFrame(eventFrameRef.current);
      eventFrameRef.current = null;
    }
    queuedEventsRef.current = [];
  }, []);

  useEffect(() => () => {
    abortRef.current?.abort();
    discardQueuedEvents();
  }, [discardQueuedEvents]);

  return { abortRef, queueEvent, flushEvents, discardQueuedEvents };
}
