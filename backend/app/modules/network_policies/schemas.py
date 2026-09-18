"""Network policy schemas — the supported StarRocks 4.1.4 subset.

Engine finding (NOVA-110, verified against the pinned 4.1.4 grammar at commit
``4a9848edf03f5c936dac664b2d52527f48e72eb0``): StarRocks 4.1.4 has **no**
``CREATE NETWORK POLICY`` object, no ``enable_ip_based_authentication`` config
and no IP allowlist/blocklist system variables. The only host-based restriction
the engine enforces is the host part of the user identity itself
(``user@'10.0.0.0/8'``), applied at connect time. That is the surface this
module models — an allowance/denial list expressed through the identity host,
plus read-only connection-source tracking.

``docs/gap-analysis.md`` §5 asks for a Snowflake-style policy object; the object
part is ``DEFER``-ed (see the module docstring) and the request models below are
the subset the engine can actually enforce. No request may carry a credential.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class PolicyTier(StrEnum):
    """Which StarRocks identity form a network rule is written as.

    StarRocks matches a connecting client against the identity host:
    ``ALLOW`` produces ``user@'<host-pattern>'`` identities that are meant to
    connect; ``DENY`` is the same engine mechanism with the semantics Nova
    records (a denied source must not resolve to a granted identity).
    """

    ALLOW = "allow"
    DENY = "deny"


def _validate_host_pattern(value: str) -> str:
    """Accept a literal IP, a CIDR block, or the engine's ``%`` wildcard.

    StarRocks identity hosts are matched with ``%`` and ``_`` wildcards; the
    practical allowlist unit is a single address or a CIDR range. Anything else
    is refused so an operator cannot smuggle a stray quote into the identity
    (the identity is interpolated into ``CREATE USER``).
    """
    candidate = value.strip()
    if not candidate:
        raise ValueError("host_pattern must not be empty")
    if candidate == "%":
        return candidate
    if "/" in candidate:
        try:
            ipaddress.ip_network(candidate, strict=False)
            return candidate
        except ValueError as exc:
            raise ValueError(f"'{value}' is not a valid CIDR block") from exc
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        # The engine also accepts a hostname in the identity host. Allow a
        # conservative hostname shape rather than rejecting a valid connect
        # source; a quote or whitespace is still refused.
        import re

        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", candidate):
            return candidate
        raise ValueError(f"'{value}' is not a valid IP, CIDR block, or hostname") from None


class NetworkRule(BaseModel):
    """A single host pattern attached to a policy tier."""

    host_pattern: str = Field(..., min_length=1, max_length=255)
    comment: str | None = Field(None, max_length=1024)

    @field_validator("host_pattern")
    @classmethod
    def _clean_host(cls, value: str) -> str:
        return _validate_host_pattern(value)


class NetworkPolicyCreate(BaseModel):
    """Create a per-user network policy.

    ``username``/``host`` identify the StarRocks user whose allowed connection
    sources this policy governs. ``allowed_hosts`` is the allowlist; an empty
    allowlist means "no restriction" (equivalent to the engine default ``%``).
    """

    name: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    username: str = Field(..., min_length=1, max_length=128)
    host: str = Field("%", max_length=255)
    allowed_hosts: list[NetworkRule] = Field(default_factory=list)
    denied_hosts: list[NetworkRule] = Field(default_factory=list)
    comment: str | None = Field(None, max_length=1024)

    @field_validator("host")
    @classmethod
    def _clean_identity_host(cls, value: str) -> str:
        return _validate_host_pattern(value)


class NetworkPolicyUpdate(BaseModel):
    """Replace a policy's rules. Omitted lists keep their current value."""

    allowed_hosts: list[NetworkRule] | None = None
    denied_hosts: list[NetworkRule] | None = None
    comment: str | None = Field(None, max_length=1024)


class NetworkPolicyResponse(BaseModel):
    """A policy as stored in Nova metadata and mirrored to engine identities.

    There is no credential-bearing field: a network rule is a host pattern, and
    the identity host is not a secret.
    """

    name: str
    username: str
    host: str
    allowed_hosts: list[NetworkRule] = Field(default_factory=list)
    denied_hosts: list[NetworkRule] = Field(default_factory=list)
    comment: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None


class NetworkPolicyListResponse(BaseModel):
    policies: list[NetworkPolicyResponse]
    count: int


class ConnectionSource(BaseModel):
    """A recent successful login source, read from ``NOVA_SYSTEM.AUDIT_LOG``."""

    user_name: str
    last_seen: datetime | None = None
    events: int = 0
