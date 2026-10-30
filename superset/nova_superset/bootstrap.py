"""Idempotent Superset bootstrap for Nova, run after ``superset init``.

* Rebuilds the three Nova roles from Superset's built-in roles minus every
  permission Nova keeps for itself (S6-S8): user and role administration,
  row-level security, database management, uploads, imports, dynamic plugins
  and guest tokens. Data access is granted broadly because Ranger decides
  what each principal sees.
* Provisions the single "Nova" database, pointing at Nova's MySQL proxy with
  no stored credential.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("nova_superset.bootstrap")

NOVA_DATABASE = "Nova"

#: View menus no Nova role may touch.
DENIED_VIEWS = frozenset(
    {
        "Row Level Security",
        "RowLevelSecurity",
        "RoleModelView",
        "UserDBModelView",
        "UserGroupModelView",
        "Role",
        "User",
        "Group",
        "Permission",
        "PermissionViewMenu",
        "ViewMenu",
        "RoleRestAPI",
        "UserRegistrationsRestAPI",
        "ResetPasswordView",
        "UserInfoEditView",
        "DynamicPlugin",
        "ImportExportRestApi",
        "Extensions",
        "List Users",
        "List Roles",
        "List Groups",
        "User Registrations",
        "Security",
    }
)

#: Individual (permission, view menu) pairs no Nova role may hold.
DENIED_PAIRS = frozenset(
    {
        ("can_write", "Database"),
        ("can_upload", "Database"),
        ("can_export", "Database"),
        ("menu_access", "Databases"),
        ("can_grant_guest_token", "SecurityRestApi"),
        ("can_set_embedded", "Dashboard"),
        ("resetpasswords", "UserDBModelView"),
        ("resetmypassword", "UserDBModelView"),
        ("userinfoedit", "UserDBModelView"),
    }
)

#: Nova role -> built-in roles it is derived from.
ROLE_SOURCES = {
    "NovaViewer": ("Gamma",),
    "NovaAuthor": ("Alpha", "sql_lab"),
    "NovaAdmin": ("Admin",),
}

#: Granted to every Nova role: Ranger, not Superset, scopes the data.
DATA_ACCESS = (("all_datasource_access", "all_datasource_access"),)


def allowed(permission_name: str, view_menu_name: str) -> bool:
    return (
        view_menu_name not in DENIED_VIEWS and (permission_name, view_menu_name) not in DENIED_PAIRS
    )


def nova_database_uri() -> str:
    host = os.environ.get("NOVA_PROXY_HOST", "nova-backend")
    port = os.environ.get("NOVA_PROXY_PORT", "4406")
    # The user is a placeholder: the connection mutator replaces it, and the
    # password, with the signed-in principal and a session data token.
    return f"starrocks://nova@{host}:{port}/default_catalog"


def sync_roles(sm, session) -> None:
    for name, sources in ROLE_SOURCES.items():
        permissions = {}
        for source in sources:
            role = sm.find_role(source)
            if role is None:
                raise RuntimeError(f"Built-in Superset role {source} is missing")
            for pvm in role.permissions:
                if allowed(pvm.permission.name, pvm.view_menu.name):
                    permissions[pvm.id] = pvm
        for permission_name, view_menu_name in DATA_ACCESS:
            pvm = sm.find_permission_view_menu(permission_name, view_menu_name)
            if pvm is None:
                pvm = sm.add_permission_view_menu(permission_name, view_menu_name)
            permissions[pvm.id] = pvm
        role = sm.find_role(name) or sm.add_role(name)
        role.permissions = sorted(permissions.values(), key=lambda pvm: pvm.id)
        session.merge(role)
        logger.info("Role %s: %d permissions", name, len(role.permissions))
    session.commit()


def sync_database(session) -> None:
    from superset.models.core import Database

    database = session.query(Database).filter_by(database_name=NOVA_DATABASE).one_or_none()
    if database is None:
        database = Database(database_name=NOVA_DATABASE)
        session.add(database)
    database.set_sqlalchemy_uri(nova_database_uri())
    database.expose_in_sqllab = True
    # Async SQL Lab runs in Celery as the submitting session (S10).
    database.allow_run_async = True
    database.allow_dml = False
    database.allow_ctas = False
    database.allow_cvas = False
    database.allow_file_upload = False
    database.impersonate_user = False
    database.encrypted_extra = None
    session.commit()
    others = [
        db.database_name
        for db in session.query(Database).all()
        if db.database_name != NOVA_DATABASE
    ]
    if others:
        logger.warning("Databases other than Nova exist and should be removed: %s", others)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from superset.app import create_app

    app = create_app()
    with app.app_context():
        from superset import db, security_manager

        sync_roles(security_manager, db.session)
        sync_database(db.session)


if __name__ == "__main__":
    main()
