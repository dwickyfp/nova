import type { z } from "zod";
import {
  boundAppContext,
  createApplicationEvent,
  type NevaAppContext,
  type NevaApplicationEvent,
  type NevaEventInput,
  type NevaSurfaceContext,
} from "./app-context";

export type NevaSuggestedAction = { label: string; prompt: string };
export type NevaCapabilityRisk = "safe" | "draft" | "reversible" | "privileged";

/** A model can name this action, but only application code can implement it. */
export type NevaClientCapability = {
  name: string;
  risk: NevaCapabilityRisk;
  /** Navigation and tab actions may intentionally replace their source page. */
  mayChangeSurface: boolean;
  invoke: (args: unknown) => Promise<unknown>;
};

export function defineNevaCapability<T extends z.ZodType>({
  name,
  risk,
  mayChangeSurface = false,
  argsSchema,
  execute,
}: {
  name: string;
  risk: NevaCapabilityRisk;
  mayChangeSurface?: boolean;
  argsSchema: T;
  execute: (args: z.infer<T>) => unknown | Promise<unknown>;
}): NevaClientCapability {
  return {
    name,
    risk,
    mayChangeSurface,
    invoke: (args) => Promise.resolve(execute(argsSchema.parse(args))),
  };
}

export type NevaSurfaceDefinition = {
  id: string;
  route: string;
  title?: string;
  context: () => NevaSurfaceContext;
  capabilities?: readonly NevaClientCapability[];
  suggestedActions?: readonly NevaSuggestedAction[];
};

type SurfaceEntry = { get: () => NevaSurfaceDefinition };

/** Owned by AssistantProvider; registrations are revoked on page unmount. */
export class NevaSurfaceRegistry {
  private entries = new Map<symbol, SurfaceEntry>();
  private listeners = new Set<() => void>();
  private version = 0;
  private fingerprint = "";

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = () => this.version;

  register(get: () => NevaSurfaceDefinition): () => void {
    const token = Symbol("neva-surface");
    this.entries.set(token, { get });
    this.refresh(true);
    return () => {
      this.entries.delete(token);
      this.refresh(true);
    };
  }

  getActive(): NevaSurfaceDefinition | null {
    const entries = [...this.entries.values()];
    return entries.length ? entries[entries.length - 1].get() : null;
  }

  refresh(force = false): void {
    const surface = this.getActive();
    let next: string;
    try {
      next = surface ? JSON.stringify({
        id: surface.id,
        route: surface.route,
        title: surface.title,
        context: surface.context(),
        capabilities: surface.capabilities?.map((capability) => capability.name),
        actions: surface.suggestedActions,
      }) : "";
    } catch {
      next = surface?.id ?? "";
    }
    if (!force && next === this.fingerprint) return;
    this.fingerprint = next;
    this.version += 1;
    this.listeners.forEach((listener) => listener());
  }

  buildContext(
    fallback: { database?: string | null; schema?: string | null; role?: string | null },
    events: readonly NevaApplicationEvent[],
  ): NevaAppContext {
    const surface = this.getActive();
    let details: NevaSurfaceContext = {};
    try {
      details = surface?.context() ?? {};
    } catch {
      // A broken page context must not block a turn.
    }
    const route = typeof window === "undefined" ? "/" : window.location.pathname;
    const id = surface?.id ?? "nova.global";
    return boundAppContext({
      version: 1,
      surface: { id, route: surface?.route ?? route, title: surface?.title },
      ...details,
      domain: {
        database: fallback.database ?? null,
        schema: fallback.schema ?? null,
        role: fallback.role ?? null,
        ...details.domain,
      },
      capabilities: surface?.capabilities?.map((capability) => capability.name) ?? [],
      events: events.filter((event) => event.surfaceId === id),
    });
  }

  async executeAction(request: {
    capability: string;
    args: unknown;
    surfaceId?: string;
  }): Promise<void> {
    const surface = this.getActive();
    if (!surface || (request.surfaceId && request.surfaceId !== surface.id)) {
      throw new Error("The requested page is no longer active.");
    }
    const capability = surface.capabilities?.find((item) => item.name === request.capability);
    if (!capability) throw new Error("This action is not available on the current page.");
    if (capability.risk !== "safe" && capability.risk !== "draft") {
      throw new Error("This action requires the existing approval workflow.");
    }
    await capability.invoke(request.args);
    if (!capability.mayChangeSurface && this.getActive()?.id !== surface.id) {
      throw new Error("The page changed before this action completed.");
    }
  }
}

/** Small, private evidence buffer. A turn only includes events for its active page. */
export class NevaApplicationEvents {
  private events: NevaApplicationEvent[] = [];
  private listeners = new Set<() => void>();
  private version = 0;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = () => this.version;
  getRecent = (): readonly NevaApplicationEvent[] => this.events;

  publish(input: NevaEventInput): NevaApplicationEvent {
    const event = createApplicationEvent(input);
    this.events = [...this.events, event].slice(-16);
    this.version += 1;
    this.listeners.forEach((listener) => listener());
    return event;
  }
}
