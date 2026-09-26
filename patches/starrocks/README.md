# StarRocks Ranger active-role patch

Nova pins StarRocks `4.1.4` at commit
`4a9848edf03f5c936dac664b2d52527f48e72eb0`, matching Nova's existing
engine baseline and L3 regression suite. The patch is
opt-in upstream-compatible behavior; Nova enables it with:

```xml
<name>ranger.plugin.starrocks.role.context.mode</name>
<value>active_role</value>
```

In this mode every object, row-filter, and masking request contains exactly the
single role active in `ConnectContext`. Zero or multiple active roles fail
closed. Root remains an explicit StarRocks bootstrap exception and is blocked
from Nova's public query surfaces.

Verify against a clean checkout:

```bash
./patches/starrocks/verify.sh
```

The integration image in `docker/ranger/starrocks-fe.Dockerfile` applies the
Ranger patches and the task active-role patch, replacing the compiled classes in the
official runtime `fe-core-4.1.4.jar`. It also pins Nashorn 15.4 and ASM Commons
9.4 (with SHA-256 verification), because Ranger 2.8's dynamic user-attribute
expressions need a JSR-223 JavaScript engine that Java 17 no longer bundles.
The build does not vendor the StarRocks source tree.

## Task execution role

`4.1.4-task-active-role.patch` fixes upstream `TaskRun.switchUser`, which normally
activates every role assigned to the creator. The async builder now captures the submitting context. A caller-submitted task
snapshots that caller's selected role set and intersects it with current assignments, so a
revoked role cannot be resurrected. The user identity must match the submitting
context. Internal runs without a matching caller keep upstream behavior; Nova
uses one-shot submissions for its scheduled graphs under the bound service role.

This is an engine build patch, not a Java application component. The Python
worker still submits SQL through the authenticated user connection. Live
multi-role acceptance results are in
`docs/benchmarks/task-role-ownership-2026-09-26/`.
