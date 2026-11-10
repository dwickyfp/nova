# Notebooks Agent Guide

Inherit the [root](../../../../AGENTS.md) and [backend](../../../AGENTS.md)
contracts. Architecture: [arch-20](../../../../docs/arch-20-nova-notebook.md).

- A notebook is a Workspace file. `WorkspaceService` stays the only writer of
  its content; `documents.py` owns the format and revisioned edits. Never save
  outputs into the `.ipynb`.
- Live runtime state lives in Redis (`runtime/state.py`); durable rows in
  `NOVA_SYSTEM` (`repository.py`). The worker holding a runtime's lease is the
  only writer of that runtime's status and of execution rows — the API records
  intent and wakes the worker. Do not update an execution row from two places.
- Only `runtime/opensandbox_provider.py` imports the OpenSandbox SDK. Tests use
  a fake provider and fakeredis (`tests/unit/test_notebook_runtime.py`).
- Code sent to a kernel is built in `runtime/kernel_calls.py`: user text only
  travels inside JSON quoted by `repr`. Credentials never travel in code or
  environment; they go through a one-time file.
- Nova data in a notebook is governed by the proxy: a notebook data token
  (`access_control/data_tokens.py`, purpose `notebook`) bound to the session
  and the runtime. Do not give a runtime another credential; catalog
  credentials never enter the sandbox.
- Writes the kernel cannot do through the proxy (stages, Postgres, Snowflake,
  large Nova writes) are requests the worker does as the runtime's user
  (`runtime/rpc.py`). Add new request kinds there, through the owning service
  and its authorization (stage access, Ranger write-back), never with a
  service account.
- The kernel side is `docker/notebook-runtime/nova_runtime`; its tests run in
  the image: `docker build -t nova-notebook-runtime:dev docker/notebook-runtime`
  then `docker run --rm --entrypoint python nova-notebook-runtime:dev -m pytest
  -q -p no:cacheprovider /opt/nova/tests`.
- Live check against a running dev stack: `scripts/notebook_live_check.py`.
