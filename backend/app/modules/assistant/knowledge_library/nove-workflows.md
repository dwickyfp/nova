# Nove and Nova Studio workflows

Keywords: assistant, bantuan, panduan, worksheet, SQL, query, agent, studio, skill.

Nove is the built-in assistant for Nova warehouse work. Nova Studio configures
specialist agents with selected tools, skills, instructions, and semantic models.
Both use the assistant engine. A tool must be available for the current turn
before the assistant can use it. Agent configuration does not grant database access.

For SQL help, attach the relevant worksheet query and describe the desired result
or error. Nove receives attached SQL and the active database/schema context.
Do not assume it can see every open page, unsent editor change, or error.
Ask for missing SQL or error text when needed; never ask for credentials.

Nove can author SQL, execute permitted read-only queries through query_execute,
run available ML tasks, and chart verified results. Account DDL and write SQL
are drafts for the user to run. Creating a Studio agent or semantic model uses
separate tools and requires their consent policy.

A conversation read-only grant applies only to read-only actions. A write tool
still requires approval. Changing instructions or loading a skill never widens
the user's privileges.

Implementation sources: assistant/router.py, assistant/registry.py,
agents/service.py, frontend/src/features/assistant/use-assistant-turn.ts.
This is packaged product guidance, not evidence of the current deployment state.
