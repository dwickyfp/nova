"""Document action implications; these never confer source-data privileges."""

from collections.abc import Iterable

from .contracts.common import Permission

ALL_PERMISSIONS: frozenset[Permission] = frozenset({
    "view", "interact", "comment", "edit", "publish", "manage", "export",
})


def expand_permissions(
    grants: Iterable[Permission], *, owner: bool = False,
) -> frozenset[Permission]:
    if owner:
        return ALL_PERMISSIONS
    actions = set(grants)
    if "manage" in actions:
        actions.update({"view", "interact", "comment", "edit", "publish"})
    if "edit" in actions:
        actions.update({"view", "interact", "comment"})
    else:
        actions.discard("publish")
    if actions & {"interact", "comment"}:
        actions.add("view")
    return frozenset(actions)
