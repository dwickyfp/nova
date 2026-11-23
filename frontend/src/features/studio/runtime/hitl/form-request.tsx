import { useId, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { answerValues, formErrors, initialValues } from "./form-values";
import type { HitlBodyProps } from "./registry";
import type { FormField, FormRequest, FormValues } from "./types";

type FieldProps = {
  field: FormField;
  value: unknown;
  error?: string;
  required: boolean;
  disabled: boolean;
  onChange: (value: unknown) => void;
};

function FieldControl({ field, value, error, required, disabled, onChange }: FieldProps) {
  const id = useId();
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  const described = [field.description ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  const common = { "aria-invalid": Boolean(error), "aria-describedby": described, disabled };
  const label = (
    <Label htmlFor={field.widget === "date_range" ? `${id}-start` : id} className="text-xs font-medium">
      {field.title}
      {required ? <span className="text-muted-foreground"> (required)</span> : null}
    </Label>
  );

  let control;
  switch (field.widget) {
    case "select":
      control = (
        <Select value={typeof value === "string" ? value : ""} onValueChange={onChange} disabled={disabled}>
          <SelectTrigger id={id} className="h-9 w-full text-sm" {...common}>
            <SelectValue placeholder="Choose a value" />
          </SelectTrigger>
          <SelectContent>
            {field.values?.map((item) => (
              <SelectItem key={item} value={item}>
                {item}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
      break;
    case "toggle":
      control = (
        <Switch id={id} checked={value === true} onCheckedChange={(checked) => onChange(checked)} {...common} />
      );
      break;
    case "date_range": {
      const range = (value ?? {}) as { start?: string; end?: string };
      control = (
        <div className="flex flex-wrap items-center gap-2">
          <Input
            id={`${id}-start`}
            type="date"
            aria-label={`${field.title} start`}
            value={range.start ?? ""}
            onChange={(event) => onChange({ ...range, start: event.target.value })}
            className="h-9 w-auto min-w-0 flex-1 text-sm"
            {...common}
          />
          <span aria-hidden="true" className="text-muted-foreground">to</span>
          <Input
            type="date"
            aria-label={`${field.title} end`}
            value={range.end ?? ""}
            onChange={(event) => onChange({ ...range, end: event.target.value })}
            className="h-9 w-auto min-w-0 flex-1 text-sm"
            {...common}
          />
        </div>
      );
      break;
    }
    default:
      control = (
        <Input
          id={id}
          type={field.widget === "date" ? "date" : field.widget === "text" ? "text" : "number"}
          inputMode={field.widget === "integer" ? "numeric" : field.widget === "number" ? "decimal" : undefined}
          step={field.widget === "integer" ? 1 : field.widget === "number" ? "any" : undefined}
          min={field.minimum}
          max={field.maximum}
          maxLength={field.widget === "text" ? field.maxLength : undefined}
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(event) => onChange(event.target.value)}
          className="h-9 text-sm"
          {...common}
        />
      );
  }

  return (
    <div className={field.widget === "toggle" ? "flex items-center justify-between gap-3" : "flex flex-col gap-1.5"}>
      {label}
      {control}
      {field.description ? (
        <p id={hintId} className="text-xs text-muted-foreground">
          {field.description}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** A small form generated from the schema the agent supplied. */
export function FormBody({ request, submit, busy, titleId }: HitlBodyProps<FormRequest>) {
  const [values, setValues] = useState<FormValues>(() => initialValues(request));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tried, setTried] = useState(false);
  const [sending, setSending] = useState(false);

  const change = (name: string, value: unknown) => {
    const next = { ...values, [name]: value };
    setValues(next);
    if (tried) setErrors(formErrors(request, next));
  };

  return (
    <form
      aria-labelledby={titleId}
      className="mt-3 flex flex-col gap-3"
      noValidate
      onSubmit={async (event) => {
        event.preventDefault();
        setTried(true);
        const found = formErrors(request, values);
        setErrors(found);
        if (Object.keys(found).length || busy) return;
        setSending(true);
        try {
          await submit({ action: "answer", answer: { values: answerValues(request, values) } });
        } finally {
          setSending(false);
        }
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        {request.fields.map((field) => (
          <div key={field.name} className={field.widget === "date_range" || field.widget === "text" ? "sm:col-span-2" : undefined}>
            <FieldControl
              field={field}
              value={values[field.name]}
              error={errors[field.name]}
              required={request.required.includes(field.name)}
              disabled={busy}
              onChange={(value) => change(field.name, value)}
            />
          </div>
        ))}
      </div>
      <div>
        <Button type="submit" size="sm" disabled={busy}>
          {sending ? <Loader2 aria-hidden="true" className="size-3.5 animate-spin" /> : null}
          {request.submitLabel ?? "Continue"}
        </Button>
      </div>
    </form>
  );
}
