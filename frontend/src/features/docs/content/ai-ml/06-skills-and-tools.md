---
title: Skills and tools
description: Add a reusable playbook and understand the tools supplied by connected MCP servers.
---

## What they are

A **skill** is a written playbook an agent can load for a task. A **tool** is an
operation it can call. Writing a skill does not create a tool or grant data
access.

## Add a skill

1. Open **AI & ML → Skills** to reach **Skill Registry**.
2. Click **Add skill**.
3. Enter **Name** using lowercase words separated by hyphens, and add a
   **Description**.
4. Edit **SKILL.md**. Describe when to use the playbook, the steps to follow,
   and the evidence the answer should include. The form provides a template.
5. Click **Add skill** and check that it appears in the registry.
6. Open an agent's **Configuration → Skills** to review its skill choices.

Keep secrets and personal credentials out of playbooks. Instructions in a
skill do not bypass the user's permissions or approval requirements.

## Inspect tools

Open **AI & ML → Tools** to reach **Tools Registry**. **Tools** lists tools
provided by connected servers. **MCP servers** lists the connections that
supply them. An empty registry means no connected tools are available there.

MCP is a protocol for connecting an agent to another service's tools. Before
adding a server, obtain its supported connection details from your
administrator and switch your active role to **ACCOUNTADMIN**. Having that role
assigned while another role is active does not allow connector changes.
**Add MCP server** asks for **Name**, **Transport**,
**Endpoint URL**, and **Description**. A registered connection still needs a
reachable, compatible server; entering a name alone cannot make tools usable.

Click **Discover** on the server to register its tools. Discovered tools appear
in **Tools** and in an agent's **Configuration → Tools → MCP tools**. Select the
tools the agent needs and save its configuration. **Configuration → MCP** lists
the connected servers. Changing a shared tool's enabled state also requires the
active **ACCOUNTADMIN** role.

Selected HTTP tools ask for approval on each call. Review the target and
expected effect before approving. Tool output is evidence to inspect, not
permission to perform another action. If the registry fails to load, use
**Retry**; a loading failure does not mean the registry is empty.
