# Notebooks Agent Guide

Inherit the [root](../../../../AGENTS.md) and [backend](../../../AGENTS.md)
contracts. Architecture: [arch-20](../../../../docs/arch-20-nova-notebook.md).
Also read the [Workspace guide](../workspaces/AGENTS.md) for file changes,
[runtime guide](../../worker/AGENTS.md) for controller/process changes and
[metadata guide](../../../metadata_migrations/AGENTS.md) for PG ports.

- A notebook is a Workspace file. `WorkspaceService` stays the only writer of
  its content; `documents.py` owns the format and revisioned edits. Never save
  outputs into the `.ipynb`.
- Current live runtime state lives in Redis (`runtime/state.py`); durable rows
  are still in `NOVA_SYSTEM` (`repository.py`) until the approved PG cutover.
  The target moves durable generations, claims and execution state to PG;
  Redis becomes a wakeup/cache transport. Follow the root migration contract,
  preserve per-person runtime identity, and do not add dual-write or a fallback.
  The worker holding a runtime's lease is the
  only writer of that runtime's status and of execution rows. The API records
  intent and wakes the worker. Do not update an execution row from two places.
- A notebook may sit in a shared workspace. Resolve it through
  `workspaces/spaces.py` (`space_service.for_entry`, `edit=True` for changes),
  never by the caller's username alone. Runtime keys are per notebook and
  person (`state.channel`): never key runtime state by notebook id alone, or
  one person would run under another's identity.
- Only `runtime/opensandbox_provider.py` imports the OpenSandbox SDK. Tests use
  a fake provider and fakeredis (`tests/unit/test_notebook_runtime.py`).
- Code sent to a kernel is built in `runtime/kernel_calls.py`: user text only
  travels inside JSON quoted by `repr`. The scoped notebook data token uses
  the existing one-time file handoff, never code or environment. Infrastructure,
  provider and catalog credentials never enter the kernel through any channel.
- Nova data in a notebook is governed by the proxy: a notebook data token
  (`access_control/data_tokens.py`, purpose `notebook`) bound to the session
  and the runtime. Do not give a runtime another credential; catalog
  credentials never enter the sandbox.
- Writes the kernel cannot do through the proxy (stages, Postgres, Snowflake,
  large Nova writes) are requests the worker does as the runtime's user
  (`runtime/rpc.py`). Add new request kinds there, through the owning service
  and its authorization (stage access, Ranger write-back), never with a
  service account.
- Process separation does not prove runtime isolation or safe replicas. Preserve
  per-person generations, claims, fencing, token revocation and reconnect state;
  test concurrent controllers, stale workers and dependency loss. The approved
  target keeps durable lifecycle authority in PG and Redis as transport/cache.
- The kernel side is `docker/notebook-runtime/nova_runtime`; its tests run in
  the image: `docker build -t nova-notebook-runtime:dev docker/notebook-runtime`
  then `docker run --rm --entrypoint python nova-notebook-runtime:dev -m pytest
  -q -p no:cacheprovider /opt/nova/tests`.
- Live check against a running dev stack: `scripts/notebook_live_check.py`.
