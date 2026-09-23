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
    "Use Nove's inspect_role_access tool to check the role's effective Ranger access, then use grant_role_access only for permissions still missing. These tools act through Nova's internal access-control service; do not call or display HTTP API routes. StarRocks native grants do not satisfy Verify Access in full Ranger mode.",
    ...(roleName.toUpperCase() === "ACCOUNTADMIN"
      ? [
          "ACCOUNTADMIN is protected against DROP, REVOKE, and ALTER ROLE; adding a Ranger access policy for it is allowed.",
        ]
      : []),
    "Explain the changes and ask for my approval before applying them. Afterward, help me verify this agent's access again.",
  ].join("\n");
}
