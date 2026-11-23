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
skill do not bypass the user's permissions.

## Inspect tools

Open **AI & ML → Tools** to reach **Tools Registry**. **Tools** lists tools
provided by connected servers. **MCP servers** lists the connections that
supply them. An empty registry means no connected tools are available there.
If the registry fails to load, use **Retry**; a loading failure does not mean
the registry is empty.

## Connect an MCP server

MCP is a protocol for connecting an agent to another service's tools. Before
adding a server, obtain its connection details from the team that runs it and
switch your active role to **ACCOUNTADMIN**. Having that role assigned while
another role is active does not allow connector changes.

1. Open **AI & ML → Tools** and choose **MCP servers**.
2. Click **Add MCP server**.
3. Enter **Name**, choose **Transport**, and enter **Endpoint URL**. The
   address can be on the internet, on your company network, or on the same
   machine as Nova.
4. If the server needs a key or token, click **Add header** under
   **Auth headers (optional)** and enter the header name (for example
   `Authorization`) and its value.
5. Add a **Description** and click **Add server**.
6. Click **Discover** on the server. Its tools appear in **Tools**.

The **Endpoint URL** starts with `http://` or `https://`, for example
`https://tools.example.com/mcp`. Put a key or token in **Auth headers**, never
in the address; an address with a user name or password is refused.

Nova runs tools only over **HTTP (Streamable)**. For a **stdio** server, enter
the **Command** that starts it instead of an endpoint. Nova saves it for
reference but never runs it: **Discover** reports "Nova does not run stdio
servers". Run the server so it answers over HTTP, then add that address as an
**HTTP (Streamable)** server.

### Auth headers

Header values are write-only. Nova stores them encrypted, sends them only to
that server's endpoint, and never shows them again: the server row lists only
the header names, for example **Auth headers: Authorization (values hidden)**.
To change them, click **Auth headers** on the server, enter the new headers and
click **Save headers**; saving replaces every saved header. **Remove all
headers** deletes them.

Nova does not follow a redirect from a connector. If Discover reports that the
server answered with a redirect, register the final address instead. A server
that refuses the request reports "Check the connector's auth headers".

> **Note:** Addresses in the link-local range, including the cloud metadata
> service, are always refused.

## How MCP tools run

A Studio agent calls the MCP tools it is configured with without asking you
first. Every call is recorded in the audit log under your name and active role,
with the values it sent; passwords, tokens and keys are hidden.

To stop a tool from being used, switch it off in **Tools**. Only the active
**ACCOUNTADMIN** role can switch a connected tool on or off. Running
**Discover** again keeps any tool you switched off switched off, and switches
off a tool the server no longer offers.

Each MCP tool in **Tools** shows what its server says about it, after **The
server says:**

| Label | Meaning |
| --- | --- |
| **Only reads** | The server says the tool reads and changes nothing. |
| **Changes data** | The server says the tool changes something. |
| **May delete or overwrite data** | The server says the tool can delete or overwrite data. |
| **Not described** | The server does not say. |

This is the server's own description; Nova does not check it.

### Scheduled automations

A scheduled automation runs with nobody there to watch, so it can only call an
MCP tool whose server says **Only reads**, such as a currency conversion or a
targets lookup. It never calls any other MCP tool, takes an action, or
schedules another automation.

## Use MCP tools in an agent

Discovered tools appear in an agent's **Configuration → Tools → MCP tools**.
Select the tools the agent needs and save its configuration.
**Configuration → MCP** lists the connected servers. Changing a shared tool's
enabled state also requires the active **ACCOUNTADMIN** role.

In Studio, a tool call shows in the run steps under the server and tool name,
for example "Used Nova test tools: fx convert". Open the step to see the exact
values the tool was sent. Tool output is evidence to
inspect, not business data and not permission to perform another action. When
a tool reports an error, the agent sees the tool's message and can explain it
or try another way. A tool that does not answer within 60 seconds stops with
"did not answer within 60 seconds"; **Stop** ends the wait at once.
