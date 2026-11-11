---
title: AI providers
description: Connect AI model providers, register their models, choose the default assistant model, and map AI SQL functions to models.
---

## What it is

Nova's AI features, such as Nova Studio, the Neva assistant, AI agents, and AI
SQL functions like `AI_COMPLETE`, call language models from an **AI provider**,
for example OpenAI or a model server you run yourself. The **AI Providers** page
is where an administrator connects those providers.

API keys you enter here are stored encrypted. After saving, Nova only ever shows
a masked hint of a key, never the key itself.

## Before you start

- You need an administrator role, such as `ACCOUNTADMIN`.
- Have the provider's endpoint address and an API key ready.

## Open the page

In the left menu, under **Administrator**, click **AI Providers**. The page has
two tabs: **Providers** and **Functions**.

## The Providers tab

At the top of the tab are two settings that apply to all of Nova:

- **Default assistant model**: the model used when an agent or conversation has
  no model chosen. **First available model** lets Nova pick. Choose a model and
  click **Save default**.
- **Tavily API key**: the key that AI agents with web search turned on use to
  search the public web. When a key is saved, it shows **Active** and a masked
  value, with **Replace key** and **Remove** buttons. When none is saved, it
  shows **Not set**; type the key and click **Save key**.

Below them, the providers table shows each provider's **Provider** name,
**Type**, **Endpoint**, masked **API Key**, number of **Models**, and **Status**.
Click a provider's row to see its models. Use **Search providers or models**
and the **Type** filter to find one.

### Add a provider

1. Click **Add Provider**.
2. Type a **Name**, for example `My vLLM`.
3. Choose the **Type**: **OpenAI**, **Anthropic**, or **OpenAI Compatible**
   (for any server that speaks the OpenAI API).
4. Type the **Endpoint**, for example `https://api.openai.com/v1`.
5. Type the **API Key**.
6. Click **Test connection**. Nova tries to reach the endpoint and, if it
   succeeds, lists the models it found. If it fails, it says why, for example
   *Cannot connect to ... check endpoint URL and network*.
7. Click **Create Provider**.

### Add a model to a provider

1. In the provider's **...** menu, click **Add Model**.
2. Type the **Model Name** exactly as the provider names it, for example
   `gpt-4o`, and optionally a **Display Name**.
3. Choose the **Type**: **LLM** (a chat model) or **Embedding** (turns text
   into vectors for search).
4. For an LLM, set **Max Tokens**. For an embedding model, set the **Logical
   alias**, **Revision**, **Dimensions**, and **Distance metric**.
5. Click **Create Model**.

Use **Edit Provider**, **Edit Model** (then **Save Changes**), **Delete Provider**, and **Delete Model**
in the **...** menus to change or remove them.

## The Functions tab

AI SQL functions such as `AI_COMPLETE`, `AI_SENTIMENT`, `AI_CLASSIFY`,
`AI_SUMMARIZE`, `AI_EXTRACT`, `AI_TRANSLATE`, `AI_FILTER`, and `AI_EMBED` need
to know which model to call. A **function alias** maps a function type to a
provider and model.

1. Click **Add Function Alias**.
2. Choose the function **Type** and type an **Alias Name**, for example
   `default_complete`.
3. Choose the **Provider** and, optionally, the **Model**. **None (use provider
   default)** uses the provider's default.
4. Optionally add a **System Prompt** and **Default Params** (JSON).
5. Tick **Set as default alias for this function type** to make SQL use it
   when no alias is named.
6. Click **Create Alias**.

**UDF Registration** shows whether each AI function is registered in the
engine (**Registered** or **Failed**).

## Limits

- Changing the default model or a provider affects every user and agent that
  relies on it. Test a new provider with **Test connection** first.
- Removing the **Tavily API key** turns off web search for every agent that
  uses it.
