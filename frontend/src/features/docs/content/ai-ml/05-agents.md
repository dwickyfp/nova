---
title: Create and configure an agent
description: Give a Studio agent its instructions, resources, and a published configuration.
---

## What it is

An agent answers questions in [Nova Studio](/docs/studio/what-is-studio).
Its configuration describes the work it should do and the tools and resources
it may use. Each Studio turn uses one selected agent.

## Before you start

You need an AI provider available to your account. For questions about business
data, prepare a Semantic View and make sure your active role can read its
sources. An agent's configuration does not grant access to the owner's data.

## Create an agent

1. Open **AI & ML → Agent** and create an agent.
2. Give it a name that describes its job and a short description.
3. Open its detail page and select **Configuration**.
4. Under **General**, review **Display name**, **Description**, and **Analysis depth**.
5. Use **Instructions** for directions that affect answers. The description is
   shown to users; it does not instruct the agent.
6. Review **Tools**, **Resources**, **Skills**, and **MCP** for the capabilities
   the agent needs.

The runtime adjusts its own routing and recovery. **Analysis depth** controls
the amount of analysis the agent can do; it is not a choice between agents.

## Save and publish

1. Click **Save** after editing the configuration.
2. In **Version history**, compare **Saved draft** with **Active configuration**.
3. Review the changed fields, then click **Publish selected version**.
4. Check the **Agent version published** confirmation.

Saving a draft and publishing it are separate steps. The published
configuration is the one to check before testing the agent in Studio.
History can describe configuration references whose dependencies have since
changed; check the resources as well as the saved settings.

## Try the agent

Click **Open in Nova Studio**. Ask a question with a clear period and measure,
then inspect the evidence supporting the answer. See
[Ask questions](/docs/studio/ask-questions) for the conversation workflow.

Use **Access** to review sharing, **Observability** to inspect runs, and
**Improve** to review quality. Sharing the agent does not replace the active
role's permissions on its underlying data.
