import type { AccessCheckItem } from "@/features/agents/api";

const privilegeByKind: Record<string, string> = {
  table: "SELECT",
  database: "USAGE",
  function: "EXECUTE",
};

export function buildAccessGrantPrompt({
  agentName,
  roleName,
  gaps,
}: {
  agentName: string;
  roleName: string;
  gaps: AccessCheckItem[];
}): string {
  const permissions = gaps.map(
    (item) =>
      `- ${privilegeByKind[item.kind] ?? item.kind.toUpperCase()} on ${JSON.stringify(item.name)}`,
  );

  return [
    `Grant the existing role ${JSON.stringify(roleName)} the Ranger permissions required by agent ${JSON.stringify(agentName)}. Keep this exact role; do not create or suggest a replacement role.`,
    "",
    "Nova Agent Verify Access found these missing Ranger permissions:",
    ...permissions,
    "",
    "First check what this role can already use, then add only the permissions that are still missing. Engine-level grants alone do not satisfy Verify Access.",
    ...(roleName.toUpperCase() === "ACCOUNTADMIN"
      ? [
          "ACCOUNTADMIN is protected against DROP, REVOKE, and ALTER ROLE; adding a Ranger access policy for it is allowed.",
        ]
      : []),
    "Explain the changes and ask for my approval before applying them. Afterward, help me verify this agent's access again.",
  ].join("\n");
}
