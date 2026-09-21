import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { Cpu } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { SelectedModel } from "./assistant-provider";
import { listModelOptions, type ModelOption } from "./model-client";

export type ModelSelectorProps = {
  selected: SelectedModel;
  onSelect: (model: SelectedModel) => void;
  disabled?: boolean;
  /** Horizontal alignment of the dropdown relative to the trigger. */
  align?: "start" | "center" | "end";
};

/** Stable key for a provider/model pair, used as the Select value. */
function optionKey(option: ModelOption): string {
  return `${option.providerId}::${option.model}`;
}

function parseKey(key: string): SelectedModel {
  const [providerId, model] = key.split("::");
  if (!providerId || !model) return null;
  return { providerId, model };
}

/**
 * Compact model picker for the assistant header. Options come from the active
 * AI providers' registered LLM models; the selection is sent with each turn.
 * When nothing is selected the backend picks the provider's first active model,
 * so the trigger shows a neutral label rather than forcing a choice.
 */
export function ModelSelector({
  selected,
  onSelect,
  disabled,
  align = "center",
}: ModelSelectorProps) {
  const { data: options = [], isLoading } = useQuery({
    queryKey: ["assistant-model-options"],
    queryFn: listModelOptions,
    staleTime: 60_000,
  });

  const selectedKey = selected
    ? `${selected.providerId}::${selected.model}`
    : undefined;

  // Drop a stale selection (provider/model removed or deactivated) so the next
  // turn does not send an unknown model to the backend. The ref keeps this to
  // one call per stale value: if the parent ignores the update (or a test passes
  // a fixed selection) the effect must not fire on every render.
  const clearedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selected || isLoading) return;
    const stillValid = options.some(
      (option) => optionKey(option) === selectedKey,
    );
    if (stillValid) {
      clearedRef.current = null;
      return;
    }
    if (clearedRef.current === selectedKey) return;
    clearedRef.current = selectedKey ?? null;
    onSelect(null);
  }, [isLoading, onSelect, options, selected, selectedKey]);

  const byKey = useMemo(() => {
    const map = new Map<string, ModelOption>();
    for (const option of options) map.set(optionKey(option), option);
    return map;
  }, [options]);

  const label = selected
    ? (byKey.get(selectedKey ?? "")?.label ?? selected.model)
    : "Default model";

  return (
    <Select
      value={selectedKey ?? ""}
      onValueChange={(key) => onSelect(parseKey(key))}
      disabled={disabled || isLoading || options.length === 0}
    >
      <SelectTrigger
        size="sm"
        className="h-7 max-w-[11rem] min-w-0 gap-1.5 border-none bg-transparent px-1.5 text-xs shadow-none focus-visible:ring-0 dark:bg-transparent"
        aria-label="Model"
      >
        <Cpu
          aria-hidden="true"
          className="size-3.5 shrink-0 text-muted-foreground"
        />
        <SelectValue placeholder="Default model">{label}</SelectValue>
      </SelectTrigger>
      <SelectContent align={align} className="max-h-72">
        {options.map((option) => (
          <SelectItem key={optionKey(option)} value={optionKey(option)}>
            <span className="flex flex-col items-start">
              <span>{option.label}</span>
              <span className="text-xs text-muted-foreground">
                {option.providerName}
              </span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
