# Nove and Nova Studio workflows

Keywords: assistant, bantuan, panduan, worksheet, SQL, query, agent, studio, skill.

Nove is the built-in assistant for Nova warehouse work. Nova Studio configures
specialist agents with selected tools, skills, instructions, and published Semantic Views.
Both use the assistant engine. A tool must be available for the current turn
before the assistant can use it. Agent configuration does not grant database access.

For SQL help, a Nove-aware page sends a bounded description of its active surface.
The SQL Workspace can include selected SQL, the current execution status and
error, and a recent matching execution event with safe SQL text. Nove does not
receive the whole editor, full query result rows, or every open page. When a
failed statement or error is unavailable, ask for that detail; never ask for
credentials. A proposed editor rewrite is not a verified fix until the patch is
applied and a correlated rerun succeeds.

In Nova Studio, Nove can inspect the current agent's owner-scoped configuration
to explain selected tools, skills, and Semantic Views. It does not become that
Studio agent or read the agent's conversation to answer configuration questions.

Nove can author SQL, execute permitted read-only queries through query_execute,
run available ML tasks, and chart verified results. Account DDL and write SQL
are drafts when the user asks for SQL. Explicit execution uses query_mutate
with approval; account provisioning uses provision_user with protected
temporary-password input and mandatory first-login change. Tools call built-in
functions or the SQL service, never generic API routes. Creating a Studio agent or Semantic View uses
separate tools and requires their consent policy.

A conversation read-only grant applies only to read-only actions. A write tool
still requires approval. Changing instructions or loading a skill never widens
the user's privileges.

Implementation sources: assistant/router.py, assistant/registry.py,
agents/service.py, frontend/src/features/assistant/use-assistant-turn.ts.
This is packaged product guidance, not evidence of the current deployment state.
