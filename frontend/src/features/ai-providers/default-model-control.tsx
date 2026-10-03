import { useCallback, useEffect, useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";

type ModelOption = {
  id: string;
  name: string;
  display_name: string | null;
  type: string;
  is_active: boolean;
};
type ProviderOption = {
  id: string;
  name: string;
  is_active: boolean;
  models: ModelOption[];
};
type Settings = { model_id: string | null };

export function DefaultModelControl({
  providers,
  loading,
}: {
  providers: ProviderOption[];
  loading: boolean;
}) {
  const user = useAuthStore((state) => state.auth.user);
  const canEdit = [
    "ACCOUNTADMIN",
    "SECURITYADMIN",
    "user_admin",
    "security_admin",
  ].includes(user?.activeRole ?? "");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [selection, setSelection] = useState("automatic");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const id = useId();
  const options = providers
    .filter((provider) => provider.is_active)
    .flatMap((provider) =>
      provider.models
        .filter((model) => model.is_active && model.type === "llm")
        .map((model) => ({
          id: model.id,
          label: `${model.display_name || model.name} · ${provider.name}`,
        })),
    );
  const load = useCallback(async () => {
    setError(null);
    try {
      const value = await api.get<Settings>("/ai/default-model");
      setSettings(value);
      setSelection(value.model_id || "automatic");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Unable to load the default model.",
      );
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load, user?.username, user?.activeRole]);
  const unavailable =
    settings?.model_id &&
    !options.some((option) => option.id === settings.model_id);
  const save = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const value = await api.put<Settings>("/ai/default-model", {
        model_id: selection === "automatic" ? null : selection,
      });
      setSettings(value);
      setSaved(true);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Unable to save the default model.",
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <section
      aria-labelledby={`${id}-label`}
      className="space-y-3 rounded-md border p-4"
    >
      <div className="space-y-1">
        <Label id={`${id}-label`} htmlFor={id}>
          Default assistant model
        </Label>
        <p id={`${id}-description`} className="text-sm text-muted-foreground">
          Used when an agent or conversation has no explicit model selection.
        </p>
      </div>
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
        <Select
          value={selection}
          onValueChange={(value) => {
            setSelection(value);
            setSaved(false);
          }}
          disabled={!canEdit || !settings || loading || saving}
        >
          <SelectTrigger
            id={id}
            aria-describedby={`${id}-description`}
            className="w-full min-w-0 sm:max-w-lg"
          >
            <SelectValue placeholder="Loading default model…" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="automatic">First available model</SelectItem>
            {unavailable && (
              <SelectItem value={settings!.model_id!} disabled>
                Configured model unavailable
              </SelectItem>
            )}
            {options.map((option) => (
              <SelectItem key={option.id} value={option.id}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {canEdit && (
          <Button
            onClick={() => void save()}
            disabled={
              !settings ||
              saving ||
              loading ||
              selection === (settings.model_id || "automatic")
            }
          >
            {saving ? "Saving…" : "Save default"}
          </Button>
        )}
      </div>
      {!canEdit && (
        <p className="text-sm text-muted-foreground">
          An administrator can change the default model.
        </p>
      )}
      {unavailable && !loading && (
        <p className="text-sm text-destructive">
          The configured model is unavailable. Choose an active language model
          to resume default assistant requests.
        </p>
      )}
      {error && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 text-sm text-destructive"
        >
          <span>{error}</span>
          {!settings && (
            <Button size="sm" variant="outline" onClick={() => void load()}>
              Retry
            </Button>
          )}
        </div>
      )}
      <p role="status" className="text-sm text-muted-foreground">
        {saved ? "Default model saved." : ""}
      </p>
    </section>
  );
}
