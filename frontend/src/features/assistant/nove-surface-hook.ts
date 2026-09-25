import { createContext, useCallback, useContext, useEffect, useRef } from "react";
import type { NoveEventInput } from "./app-context";
import type { AttachContext } from "./query-attach";
import type { NoveSurfaceDefinition } from "./surface-registry";

export type NoveSurfaceServices = {
  register: (get: () => NoveSurfaceDefinition) => () => void;
  refresh: () => void;
  publish: (input: NoveEventInput) => void;
  ask: (prompt: string, attachment?: AttachContext) => Promise<void>;
};

export const NoveSurfaceContext = createContext<NoveSurfaceServices | null>(null);

/** A page owns its context and safe capabilities for its mounted lifetime. */
export function useNoveSurface(definition: NoveSurfaceDefinition): {
  publishEvent: (input: Omit<NoveEventInput, "surfaceId">) => void;
  askNove: (prompt: string, attachment?: AttachContext) => Promise<void>;
  refreshContext: () => void;
} {
  const services = useContext(NoveSurfaceContext);
  const definitionRef = useRef(definition);
  definitionRef.current = definition;
  useEffect(() => services?.register(() => definitionRef.current), [
    services?.register,
    definition.id,
    definition.route,
  ]);
  // Context can read editor refs that change without a page navigation.
  useEffect(() => services?.refresh());
  const publishEvent = useCallback((input: Omit<NoveEventInput, "surfaceId">) => {
    services?.publish({ ...input, surfaceId: definitionRef.current.id });
    services?.refresh();
  }, [services]);
  const askNove = useCallback((prompt: string, attachment?: AttachContext) =>
    services?.ask(prompt, attachment) ?? Promise.resolve(), [services]);
  const refreshContext = useCallback(() => services?.refresh(), [services]);
  return { publishEvent, askNove, refreshContext };
}
