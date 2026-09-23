import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Provider = {
  id: string;
  name: string;
  is_active: boolean;
  has_api_key: boolean;
};

type Model = {
  id: string;
  name: string;
  display_name: string | null;
  type: string;
  is_active: boolean;
};

export function AgentModelSelect({
  providerId,
  modelName,
  onChange,
}: {
  providerId: string | null;
  modelName: string | null;
  onChange: (providerId: string | null, modelName: string | null) => void;
}) {
  const modelsQuery = useQuery({
    queryKey: ["agents", "available-models"],
    queryFn: async () => {
      const { providers } = await api.get<{ providers: Provider[] }>(
        "/ai/providers",
      );
      const groups = await Promise.all(
        providers
          .filter((p) => p.is_active && p.has_api_key)
          .map(async (provider) => {
            const { models } = await api.get<{ models: Model[] }>(
              `/ai/providers/${encodeURIComponent(provider.id)}/models`,
            );
            return models
              .filter((model) => model.is_active && model.type === "llm")
              .map((model) => ({ ...model, provider }));
          }),
      );
      return groups.flat();
    },
  });
  const models = modelsQuery.data ?? [];
  const selected = models.find(
    (model) => model.provider.id === providerId && model.name === modelName,
  );
  const hasSelection = Boolean(providerId || modelName);

  return (
    <div className="space-y-2">
      <Label htmlFor="c-model">Model</Label>
      <Select
        value={selected?.id ?? (hasSelection ? "__saved__" : "__default__")}
        disabled={modelsQuery.isPending || modelsQuery.isError}
        onValueChange={(value) => {
          const model = models.find((item) => item.id === value);
          if (value === "__default__") onChange(null, null);
          else if (model) onChange(model.provider.id, model.name);
        }}
      >
        <SelectTrigger
          id="c-model"
          className="w-full max-w-md"
          aria-describedby="c-model-help"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__default__">Provider default</SelectItem>
          {hasSelection && !selected ? (
            <SelectItem value="__saved__" disabled>
              {modelName ?? "Provider default"}
              {modelsQuery.isSuccess ? " (unavailable)" : " (saved)"}
            </SelectItem>
          ) : null}
          {models.map((model) => (
            <SelectItem key={model.id} value={model.id}>
              {model.display_name || model.name} · {model.provider.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p
        id="c-model-help"
        className="text-xs text-muted-foreground"
        role={modelsQuery.isError ? "alert" : undefined}
      >
        {modelsQuery.isPending
          ? "Loading available models…"
          : modelsQuery.isError
            ? "Could not load models. Your saved selection is unchanged."
            : models.length === 0
              ? "No active language models available. Add a model under AI Providers."
              : "Choose a model for this agent, or use the provider default."}
      </p>
      {modelsQuery.isError ? (
        <Button
          variant="outline"
          size="sm"
          onClick={() => void modelsQuery.refetch()}
        >
          Retry
        </Button>
      ) : null}
    </div>
  );
}
