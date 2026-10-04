import { StatusBadge } from "@/components/ui/status-badge";
import type { StudioRuntimeCapabilities } from "@/features/agents/api";

const features = [
  {
    key: "business_workflow",
    label: "Mission workflow",
    description: "Investigations, scenarios, Decisions, and deliverables.",
  },
  {
    key: "actions",
    label: "Actions",
    description:
      "Reviewed changes with consent and configuration verification.",
  },
  {
    key: "quality",
    label: "Agent quality monitoring",
    description: "Production scoring for agents that have opted in.",
  },
  {
    key: "analysis_workspace",
    label: "Analysis workspace",
    description: "Bounded code analysis using granted resources.",
  },
] as const;

export function StudioRuntimeCapabilitiesPanel({
  runtime,
}: {
  runtime?: StudioRuntimeCapabilities | null;
}) {
  if (!runtime) {
    return (
      <p className="py-10 text-sm text-muted-foreground">
        This deployment does not report business capability status. Ask your
        administrator to update Nova.
      </p>
    );
  }
  return (
    <>
      <p className="text-sm leading-relaxed text-muted-foreground">
        Deployment availability. Data access, review, and consent are checked
        when you use a capability.
      </p>
      <dl className="mt-5 divide-y">
        {features.map(({ key, label, description }) => {
          const feature = runtime[key];
          return (
            <div key={key} className="flex flex-wrap items-start gap-3 py-5">
              <dt className="min-w-0 flex-1 basis-52">
                <span className="block text-sm font-medium">{label}</span>
                <span className="mt-1 block text-sm leading-relaxed text-muted-foreground">
                  {description}
                </span>
              </dt>
              <dd className="flex max-w-full flex-col items-start gap-2 sm:items-end">
                <StatusBadge
                  tone={
                    feature.available
                      ? "success"
                      : feature.enabled
                        ? "warning"
                        : "neutral"
                  }
                >
                  {feature.available
                    ? "Available"
                    : feature.enabled
                      ? "Unavailable"
                      : "Disabled"}
                </StatusBadge>
                <span className="text-xs text-muted-foreground">
                  {feature.enabled
                    ? "Enabled for this deployment"
                    : "Disabled for this deployment"}
                </span>
                {feature.executor_available === false ? (
                  <span className="max-w-64 text-sm leading-relaxed text-muted-foreground sm:text-right">
                    An isolated executor is not configured.
                  </span>
                ) : null}
              </dd>
            </div>
          );
        })}
      </dl>
    </>
  );
}
