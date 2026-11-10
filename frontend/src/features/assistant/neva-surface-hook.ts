import { createContext, useCallback, useContext, useEffect, useRef } from "react";
import type { NevaEventInput } from "./app-context";
import type { AttachContext } from "./query-attach";
import type { NevaSurfaceDefinition } from "./surface-registry";

export type NevaSurfaceServices = {
  register: (get: () => NevaSurfaceDefinition) => () => void;
  refresh: () => void;
  publish: (input: NevaEventInput) => void;
  ask: (prompt: string, attachment?: AttachContext) => Promise<void>;
};

export const NevaSurfaceContext = createContext<NevaSurfaceServices | null>(null);

/** A page owns its context and safe capabilities for its mounted lifetime. */
export function useNevaSurface(definition: NevaSurfaceDefinition): {
  publishEvent: (input: Omit<NevaEventInput, "surfaceId">) => void;
  askNeva: (prompt: string, attachment?: AttachContext) => Promise<void>;
  refreshContext: () => void;
} {
  const services = useContext(NevaSurfaceContext);
  const definitionRef = useRef(definition);
  definitionRef.current = definition;
  useEffect(() => services?.register(() => definitionRef.current), [
    services?.register,
    definition.id,
    definition.route,
  ]);
  // Context can read editor refs that change without a page navigation.
  useEffect(() => services?.refresh());
  const publishEvent = useCallback((input: Omit<NevaEventInput, "surfaceId">) => {
    services?.publish({ ...input, surfaceId: definitionRef.current.id });
    services?.refresh();
  }, [services]);
  const askNeva = useCallback((prompt: string, attachment?: AttachContext) =>
    services?.ask(prompt, attachment) ?? Promise.resolve(), [services]);
  const refreshContext = useCallback(() => services?.refresh(), [services]);
  return { publishEvent, askNeva, refreshContext };
}
