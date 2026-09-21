"""Agent Studio — build-your-own agents, semantic models, and user skills (Phase 12).

Scope and contracts are frozen in
``docs/specs/nova-12-agent-studio-implementation-plan.md``.

This module is deliberately **separate** from ``app.modules.assistant`` (Phase
10, NOVA-61): the bounded assistant loop is stable and must not change shape when
agent configuration is added. Agent Studio *composes* that loop — it builds a
per-agent tool registry and system prompt, then calls ``AssistantLoop`` — rather
than modifying it.

Design rules enforced here, not merely documented:

* **No credential may enter persistent state.** Every table here holds
  configuration and metadata only. No column holds a statement, a result row, a
  password, a token, or an API key. Agent and semantic-model definitions are
  screened for credential-shaped values before they are stored.
* **Semantic models are Ossie documents, pinned by version.** The parser accepts
  only a supported specification version and fails closed on anything else; the
  stored ``definition`` is the parsed metadata, never raw secret-bearing text.
* **Tools are registered per agent.** A tool the agent's owner did not select is
  not merely hidden by a prompt — it is absent from the registry the loop is
  built with, so the model cannot call it.
"""
