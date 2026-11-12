"""Engine-side schema still created at web startup for the Intelligence domain.

Intelligence revision stores, Business Events and News feedback are PostgreSQL
control metadata (``nova_meta``, revisions 0022 and 0025) installed by the
one-shot migrator; nothing here creates them. Only the business-policy table,
owned by access control, is still ensured from this hook.
"""

from app.core.database import db


async def ensure_engine_schema() -> None:
    from app.modules.access_control.business_policy import BUSINESS_POLICY_DDL

    await db.execute_system(BUSINESS_POLICY_DDL)
