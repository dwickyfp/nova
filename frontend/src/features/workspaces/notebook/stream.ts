import { useEffect, useRef } from "react";
import { apiBase, authHeaders } from "@/lib/api-client";

export type NotebookEvent = {
  id: string | null;
  event: string;
  data: Record<string, unknown>;
};

/** Parse SSE blocks, keeping the `id:` line the server uses for resume. */
export function parseSseBlock(block: string): NotebookEvent | null {
  let id: string | null = null;
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try {
    return { id, event, data: JSON.parse(data.join("\n")) };
  } catch {
    return null;
  }
}

export async function* readNotebookEvents(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<NotebookEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let separator = buffer.indexOf("\n\n");
      while (separator !== -1) {
        const parsed = parseSseBlock(buffer.slice(0, separator));
        buffer = buffer.slice(separator + 2);
        if (parsed) yield parsed;
        separator = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Follow a notebook's events while it is open. Reconnects after a dropped
 * connection and resumes after the last event seen, so output is never lost
 * or shown twice. `onEvent` may change between renders.
 */
export function useNotebookEvents(
  notebookId: string,
  onEvent: (event: NotebookEvent) => void,
) {
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    const controller = new AbortController();
    let lastId: string | null = null;
    let attempt = 0;

    async function connect() {
      while (!controller.signal.aborted) {
        try {
          const response = await fetch(
            `${apiBase()}/notebooks/${encodeURIComponent(notebookId)}/events`,
            {
              headers: authHeaders({
                Accept: "text/event-stream",
                ...(lastId ? { "Last-Event-ID": lastId } : {}),
              }),
              signal: controller.signal,
            },
          );
          if (!response.ok || !response.body) {
            throw new Error(`events ${response.status}`);
          }
          attempt = 0;
          for await (const event of readNotebookEvents(response.body)) {
            if (event.id) lastId = event.id;
            handler.current(event);
          }
          // The server ends each stream after a while; reconnect at once.
          continue;
        } catch {
          if (controller.signal.aborted) return;
        }
        attempt += 1;
        const delay = Math.min(1000 * attempt, 5000);
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }

    void connect();
    return () => controller.abort();
  }, [notebookId]);
}
