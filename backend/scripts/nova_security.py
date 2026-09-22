"""Inventory, migrate, and verify StarRocks marker roles against Ranger."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass, field

from app.common.identifiers import check_identifier
from app.core.database import db
from app.integrations.ranger.client import ranger_client
from app.integrations.ranger.compiler import compile_access_policy
from app.integrations.ranger.schemas import RangerRole, RangerRoleMember

GRANT_RE = re.compile(
    r"GRANT\s+(?P<access>[A-Z ,]+)\s+ON\s+(?:TABLE\s+)?"
    r"(?P<database>[A-Za-z_][A-Za-z0-9_$]*)\."
    r"(?P<table>[A-Za-z_*][A-Za-z0-9_$*]*)\s+TO\s+ROLE",
    re.IGNORECASE,
)


@dataclass(slots=True)
class InventoryRole:
    name: str
    grants: list[str] = field(default_factory=list)
    users: list[str] = field(default_factory=list)
    nested_roles: list[str] = field(default_factory=list)
    default_for: list[str] = field(default_factory=list)


def _principal_name(identity: str) -> str:
    match = re.match(r"^'([^']+)'@'[^']+'$", identity)
    return match.group(1) if match else identity


async def inventory() -> list[InventoryRole]:
    result = await db.execute_system("SHOW ROLES")
    roles_by_name: dict[str, InventoryRole] = {}
    for row in result.get("rows", []):
        name = str(row[0] if isinstance(row, (list, tuple)) else next(iter(row.values())))
        if not name or name.lower() in {"root", "public"}:
            continue
        safe = check_identifier(name, field="role")
        grants_result = await db.execute_system(f"SHOW GRANTS FOR ROLE `{safe}`")
        grants = [
            " ".join(str(cell) for cell in row if cell is not None)
            for row in grants_result.get("rows", [])
        ]
        roles_by_name[name] = InventoryRole(name=name, grants=grants)

    edges = await db.execute_system("SELECT FROM_ROLE, TO_USER, TO_ROLE FROM sys.role_edges")
    for row in edges.get("rows", []):
        if len(row) < 3 or not row[0]:
            continue
        child = str(row[0])
        if row[1] and child in roles_by_name:
            roles_by_name[child].users.append(_principal_name(str(row[1])))
        if row[2] and str(row[2]) in roles_by_name:
            roles_by_name[str(row[2])].nested_roles.append(child)

    defaults = await db.execute_system(
        "SELECT USER, HOST, ROLE_NAME, IS_DEFAULT, IS_MANDATORY "
        "FROM information_schema.applicable_roles"
    )
    for row in defaults.get("rows", []):
        if len(row) < 5 or str(row[3]).upper() != "YES" and str(row[4]).upper() != "YES":
            continue
        role = roles_by_name.get(str(row[2]))
        if role:
            role.default_for.append(f"'{row[0]}'@'{row[1]}'")

    for role in roles_by_name.values():
        role.users = sorted(set(role.users))
        role.nested_roles = sorted(set(role.nested_roles))
        role.default_for = sorted(set(role.default_for))
    return sorted(roles_by_name.values(), key=lambda role: role.name.lower())


def translated_policies(role: InventoryRole):
    for grant in role.grants:
        match = GRANT_RE.search(grant)
        if not match:
            continue
        accesses = [value.strip().lower() for value in match.group("access").split(",")]
        yield compile_access_policy(
            role=role.name,
            catalog="default_catalog",
            database=match.group("database"),
            table=match.group("table"),
            accesses=accesses,
        )


async def migrate(*, apply: bool) -> int:
    await db.init_system_pool()
    try:
        roles = await inventory()
        report = {
            "mode": "apply" if apply else "dry-run",
            "roles": [],
            "warnings": [
                "Non-table grants require operator review before cutover.",
                "Imported role hierarchy and default-role markers must pass verify-ranger.",
                "Native object grants are not modified by this command.",
            ],
        }
        for role in roles:
            policies = list(translated_policies(role))
            item = {
                "role": role.name,
                "members": role.users,
                "nested_roles": role.nested_roles,
                "default_for": role.default_for,
                "source_grants": role.grants,
                "ranger_policies": [policy.name for policy in policies],
            }
            if apply:
                await ranger_client.put_role(
                    RangerRole(
                        name=role.name,
                        users=[RangerRoleMember(name=user) for user in role.users],
                        roles=[RangerRoleMember(name=name) for name in role.nested_roles],
                    )
                )
                for policy in policies:
                    await ranger_client.put_policy(policy)
                item["status"] = "IMPORTED"
            else:
                item["status"] = "PLANNED"
            report["roles"].append(item)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    finally:
        await db.close_system_pool()


async def verify() -> int:
    await db.init_system_pool()
    try:
        marker_inventory = {role.name: role for role in await inventory()}
        ranger_inventory = {
            str(role.get("name")): role for role in await ranger_client.list_roles()
        }
        missing = sorted(marker_inventory.keys() - ranger_inventory.keys())
        extra = sorted(ranger_inventory.keys() - marker_inventory.keys())
        membership_drift: list[dict] = []
        hierarchy_drift: list[dict] = []
        for name in sorted(marker_inventory.keys() & ranger_inventory.keys()):
            marker = marker_inventory[name]
            ranger = ranger_inventory[name]
            ranger_users = sorted(
                str(item.get("name")) for item in ranger.get("users", []) if item.get("name")
            )
            ranger_children = sorted(
                str(item.get("name")) for item in ranger.get("roles", []) if item.get("name")
            )
            if ranger_users != marker.users:
                membership_drift.append(
                    {"role": name, "marker": marker.users, "ranger": ranger_users}
                )
            if ranger_children != marker.nested_roles:
                hierarchy_drift.append(
                    {"role": name, "marker": marker.nested_roles, "ranger": ranger_children}
                )
        report = {
            "healthy": not (missing or membership_drift or hierarchy_drift),
            "missing_ranger_roles": missing,
            "ranger_only_roles": extra,
            "membership_drift": membership_drift,
            "hierarchy_drift": hierarchy_drift,
            "explicit_default_markers": {
                name: role.default_for
                for name, role in marker_inventory.items()
                if role.default_for
            },
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["healthy"] else 2
    finally:
        await db.close_system_pool()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="nova-security")
    commands = root.add_subparsers(dest="command", required=True)
    migration = commands.add_parser("migrate-to-ranger")
    mode = migration.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    commands.add_parser("verify-ranger")
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "migrate-to-ranger":
        return asyncio.run(migrate(apply=args.apply))
    return asyncio.run(verify())


if __name__ == "__main__":
    raise SystemExit(main())
