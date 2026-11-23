import { CREATE_SKILL_COMMAND } from "./skill-document";

export type StudioCommandGroup = "Commands";

export interface StudioCommand {
  /** What the composer inserts, including the leading slash. */
  name: string;
  /** Shown after the name when the command takes text. */
  argumentHint?: string;
  description: string;
  group: StudioCommandGroup;
}

export const STUDIO_COMMANDS: readonly StudioCommand[] = [
  {
    name: CREATE_SKILL_COMMAND,
    argumentHint: "<task>",
    description: "Describe a task you repeat and draft a skill for it with Nova",
    group: "Commands",
  },
];

export const commandOptionId = (listId: string, index: number) => `${listId}-option-${index}`;

/**
 * The text after "/" while the composer holds a single slash word, or null
 * when the input is not a command being typed.
 */
export function commandQuery(value: string): string | null {
  if (!value.startsWith("/") || /\s/.test(value)) return null;
  return value.slice(1).toLowerCase();
}

export function matchCommands(
  query: string,
  commands: readonly StudioCommand[] = STUDIO_COMMANDS,
): StudioCommand[] {
  query = query.toLowerCase();
  const starts = commands.filter((c) => c.name.slice(1).toLowerCase().startsWith(query));
  const contains = commands.filter(
    (c) => !starts.includes(c) && c.name.slice(1).toLowerCase().includes(query),
  );
  // Keep each group together so list order matches keyboard order.
  const groups = [...new Set(commands.map((c) => c.group))];
  return [...starts, ...contains].sort(
    (a, b) => groups.indexOf(a.group) - groups.indexOf(b.group),
  );
}
