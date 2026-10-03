import { useId } from "react";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ScenarioSchema } from "./scenario-schema";

export function ScenarioFields({
  schema,
  values,
  onChange,
  disabled = false,
}: {
  schema: ScenarioSchema;
  values: Record<string, string | boolean>;
  onChange: (key: string, value: string | boolean) => void;
  disabled?: boolean;
}) {
  const prefix = useId();
  return (
    <div className="grid min-w-0 gap-4 sm:grid-cols-2">
      {Object.entries(schema.properties).map(([key, field]) => {
        const id = `${prefix}-${key}`;
        const label = field.title ?? key.replace(/_/g, " ");
        const required = schema.required?.includes(key);
        const numeric = field.type === "number" || field.type === "integer";
        return (
          <div key={key} className="min-w-0 space-y-2">
            <Label htmlFor={id}>
              {label}
              {required ? " *" : ""}
            </Label>
            {field.type === "boolean" ? (
              <div className="flex min-h-11 items-center">
                <Checkbox
                  id={id}
                  checked={values[key] === true}
                  disabled={disabled}
                  onCheckedChange={(checked) => onChange(key, checked === true)}
                  aria-describedby={
                    field.description ? `${id}-description` : undefined
                  }
                />
              </div>
            ) : field.enum ? (
              <Select
                value={String(values[key] ?? "")}
                disabled={disabled}
                required={required}
                onValueChange={(value) => onChange(key, value)}
              >
                <SelectTrigger
                  id={id}
                  className="min-h-11 w-full"
                  aria-describedby={
                    field.description ? `${id}-description` : undefined
                  }
                >
                  <SelectValue placeholder="Choose a registered value" />
                </SelectTrigger>
                <SelectContent>
                  {field.enum.map((value) => (
                    <SelectItem key={String(value)} value={String(value)}>
                      {String(value).replace(/_/g, " ")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Input
                id={id}
                className="min-h-11"
                type={numeric ? "number" : "text"}
                step={field.type === "integer" ? 1 : "any"}
                min={field.minimum ?? field.exclusiveMinimum}
                max={field.maximum ?? field.exclusiveMaximum}
                minLength={field.minLength}
                maxLength={field.maxLength ?? (numeric ? undefined : 10000)}
                required={required}
                disabled={disabled}
                value={String(values[key] ?? "")}
                onChange={(event) => onChange(key, event.target.value)}
                aria-describedby={
                  field.description ? `${id}-description` : undefined
                }
              />
            )}
            {field.description && (
              <p
                id={`${id}-description`}
                className="text-sm text-muted-foreground"
              >
                {field.description}
              </p>
            )}
            {field.unit && (
              <p className="text-xs text-muted-foreground">
                Unit: {field.unit}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
