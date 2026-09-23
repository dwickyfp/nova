import type { Skill } from "@/features/agents/api";

export const CREATE_SKILL_COMMAND = "/create-skill-with-chat";
export const MAX_SKILL_BYTES = 25 * 1024 * 1024;
export const SKILL_AUTHOR_ID = "nova-skill-author";

export function skillDocument(skill: Skill): string {
  if (skill.body.trimStart().startsWith("---")) return skill.body;
  return `---\nname: ${JSON.stringify(skill.name)}\ndescription: ${JSON.stringify(skill.description)}\n---\n\n${skill.body}`;
}

export function extractSkillDraft(answer: string): string | null {
  const block =
    /^(`{3,})(?:skill|markdown|md)[ \t]*\r?\n(---\s*\n[\s\S]*?)\r?\n\1[ \t]*$/im.exec(
      answer,
    );
  return block?.[2] ?? null;
}
