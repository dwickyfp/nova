"""Feature store metadata schema.

Feature Views, Groups, their versions, training sets and model links live in
the PG metadata store (revision 0025, ``nova_meta.config_feature_*``), applied
by the metadata migrator, never by application startup.
"""


async def ensure_feature_schema() -> None:
    """Kept for the startup call: there is no ``NOVA_SYSTEM`` feature DDL to apply."""
    return None
