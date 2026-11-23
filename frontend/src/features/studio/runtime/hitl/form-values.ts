/**
 * Client-side checks for a form answer. They mirror `validate_answer` in
 * `backend/app/modules/assistant/human_input.py` so a mistake is shown next to
 * its field; the server checks the answer again and its decision is final.
 */
import type { FormField, FormRequest, FormValues } from "./types";

const DATE = /^\d{4}-\d{2}-\d{2}$/;

function isDate(value: unknown): value is string {
  if (typeof value !== "string" || !DATE.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function empty(field: FormField, value: unknown): boolean {
  if (value === undefined || value === null || value === "") return true;
  if (field.widget === "date_range") {
    const range = value as { start?: string; end?: string };
    return !range.start && !range.end;
  }
  return false;
}

/** The starting values: each field's default, a toggle off. */
export function initialValues(request: FormRequest): FormValues {
  const values: FormValues = {};
  for (const field of request.fields) {
    if (field.default !== undefined) values[field.name] = field.default;
    else if (field.widget === "toggle") values[field.name] = false;
    else if (field.widget === "date_range") values[field.name] = { start: "", end: "" };
  }
  return values;
}

function fieldError(field: FormField, value: unknown, required: boolean): string | null {
  if (empty(field, value)) return required ? `${field.title} is required.` : null;
  switch (field.widget) {
    case "select":
      return field.values?.includes(String(value)) ? null : "Choose one of the listed values.";
    case "text":
      return String(value).length > (field.maxLength ?? 200) ? `${field.title} is too long.` : null;
    case "date":
      return isDate(value) ? null : "Enter a date.";
    case "date_range": {
      const range = value as { start?: string; end?: string };
      if (!isDate(range.start) || !isDate(range.end)) return "Enter a start and an end date.";
      return range.start > range.end ? "The start date is after the end date." : null;
    }
    case "number":
    case "integer": {
      const number = typeof value === "number" ? value : Number(value);
      if (!Number.isFinite(number)) return `${field.title} must be a number.`;
      if (field.widget === "integer" && !Number.isInteger(number)) return `${field.title} must be a whole number.`;
      if (field.minimum !== undefined && number < field.minimum) return `${field.title} must be at least ${field.minimum}.`;
      if (field.maximum !== undefined && number > field.maximum) return `${field.title} must be at most ${field.maximum}.`;
      return null;
    }
    case "toggle":
      return typeof value === "boolean" ? null : `${field.title} must be on or off.`;
  }
}

/** Errors by field name; empty when the answer can be sent. */
export function formErrors(request: FormRequest, values: FormValues): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const field of request.fields) {
    const error = fieldError(field, values[field.name], request.required.includes(field.name));
    if (error) errors[field.name] = error;
  }
  return errors;
}

/** The values as the server takes them: numbers as numbers, empty fields left out. */
export function answerValues(request: FormRequest, values: FormValues): FormValues {
  const out: FormValues = {};
  for (const field of request.fields) {
    const value = values[field.name];
    if (empty(field, value)) continue;
    out[field.name] = field.widget === "number" || field.widget === "integer" ? Number(value) : value;
  }
  return out;
}
