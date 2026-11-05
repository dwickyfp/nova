"""SSRF guard for user-supplied outbound HTTP endpoints.

Nova lets an authenticated user name an arbitrary ``endpoint`` for an AI
provider and then performs a server-side ``httpx`` GET to it
(``ai_ml.service.test_connection``). Without a guard that is a textbook SSRF
primitive: the user can point Nova at the loopback interface, at RFC1918
addresses inside the deployment network, or at the cloud metadata service
(``169.254.169.254``) and read the response back through the API.

The guard is split into two responsibilities so each is testable on its own:

1. :func:`resolve_and_validate_url` — parse the URL, resolve every hostname
   with the system resolver, and reject the URL unless *every* resolved
   address is a public, globally-routable address. The check is on the
   resolved address, not on the hostname string, so ``evil.example`` that
   resolves to ``127.0.0.1`` is caught.
3. :func:`guarded_async_client` — an ``httpx.AsyncClient`` whose transport
   resolves through the same guard. This closes the DNS-rebinding /
   redirect TOCTOU window: the address that was validated is the address
   actually connected to, and each redirect hop is re-validated.

Fail-closed: a hostname that cannot be resolved, a URL with no host, and a
non-``http(s)`` scheme are all rejected.

AI provider endpoints are written by administrators and commonly point at a
model served on the same machine or network (Ollama, LM Studio, vLLM). Those
callers pass ``allow_private_network=True``, which also accepts loopback and
private (RFC1918 / ``fc00::/7``) addresses. Link-local addresses, including the
cloud metadata service, and multicast, reserved, unspecified and CGNAT ranges
stay blocked in that mode.

Scope note — this module intentionally does **not** import anything from
``app.modules.*``; it is a leaf common utility (AGENTS.md: dependency
direction points inward).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

#: Schemes Nova is willing to call. Everything else (``file:``, ``gopher:``,
#: ``ftp:`` …) is rejected before any network activity.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Hostnames that are always local regardless of what DNS says. Kept as an
#: explicit pre-resolution short-circuit so the rejection reason is exact and
#: the behaviour does not depend on resolver configuration.
_LOCAL_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)


#: An IPv4 or IPv6 address object, as returned by ``ipaddress.ip_address``.
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class BlockedEndpointError(ValueError):
    """The requested endpoint is not allowed to be called.

    The message is safe to surface to the caller: it names the category of the
    block, never the resolved address or other internal detail.
    """

    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        # DNS can fail transiently. Callers may retry this condition, but must
        # still fail closed if resolution never succeeds. Policy failures such
        # as a private, loopback, or malformed endpoint are never retryable.
        self.retryable = retryable


@dataclass(frozen=True)
class ResolvedEndpoint:
    """A validated endpoint plus the set of addresses it resolved to."""

    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[IPAddress, ...]


def _is_public_address(
    address: IPAddress,
) -> bool:
    """Return True only for globally-routable unicast addresses.

    ``ipaddress`` already models the ranges that matter. The checks below are
    spelled out rather than relying on ``is_global`` alone because ``is_global``
    is the strictest predicate and we want an audit-friendly, explicit list:

    * private (RFC1918, RFC4193 ``fc00::/7``)
    * loopback (127.0.0.0/8, ::1)
    * link-local (169.254.0.0/16 **including 169.254.169.254**, fe80::/10)
    * unspecified (0.0.0.0, ::)
    * multicast, reserved, and the 100.64.0.0/10 CGNAT range
    """
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    return address.is_global


def _is_private_network_address(address: IPAddress) -> bool:
    """Return True for a loopback or private address on the local network.

    Link-local (``169.254.0.0/16`` and ``fe80::/10``, which hold the cloud
    metadata service), multicast, unspecified and reserved addresses never
    qualify. ``::1`` is checked as loopback first because ``ipaddress`` also
    files it under the reserved ``::/8`` block. An IPv4-mapped IPv6 address is
    judged by the IPv4 address it carries.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback:
        return True
    if (
        address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        return False
    return address.is_private


def _resolve_host(host: str, port: int) -> tuple[IPAddress, ...]:
    """Resolve ``host`` to every address the system resolver returns.

    A DNS failure is a block, not an allow: Nova must never fall back to
    calling a hostname whose address it could not vet.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise BlockedEndpointError("Endpoint host could not be resolved", retryable=True) from exc

    addresses: list[IPAddress] = []
    for info in infos:
        sockaddr = info[4]
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError as exc:
            raise BlockedEndpointError("Endpoint host resolved to an invalid address") from exc

    if not addresses:
        raise BlockedEndpointError("Endpoint host could not be resolved", retryable=True)
    return tuple(addresses)


def resolve_and_validate_url(
    url: str, *, allow_private_network: bool = False
) -> ResolvedEndpoint:
    """Validate ``url`` for outbound use and return its pinned resolution.

    Raises :class:`BlockedEndpointError` when the URL is malformed, uses a
    disallowed scheme, or resolves to any non-public address. When it returns,
    every address in ``ResolvedEndpoint.addresses`` is allowed, and callers
    should connect only to those addresses.

    With ``allow_private_network`` the URL may also name a loopback or private
    address (``localhost``, ``127.0.0.1``, ``192.168.x.x``,
    ``host.docker.internal``); every other non-public address is still refused.
    """
    if not url or not url.strip():
        raise BlockedEndpointError("Endpoint must not be empty")

    # ``urlsplit`` does not raise on garbage, so validate each component.
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise BlockedEndpointError("Endpoint is not a valid URL") from exc

    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise BlockedEndpointError("Endpoint must use http or https")

    host = parsed.hostname
    if not host:
        raise BlockedEndpointError("Endpoint must include a host")

    if host in _LOCAL_HOSTNAMES and not allow_private_network:
        raise BlockedEndpointError("Endpoint host is not allowed")

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise BlockedEndpointError("Endpoint has an invalid port") from exc

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        addresses = _resolve_host(host, port)
    else:
        addresses = (literal,)

    for address in addresses:
        if allow_private_network and _is_private_network_address(address):
            continue
        if not _is_public_address(address):
            logger.warning(
                "Blocked outbound test-connection attempt to non-public address %s",
                address,
            )
            raise BlockedEndpointError("Endpoint host is not allowed")

    return ResolvedEndpoint(
        url=url,
        scheme=scheme,
        host=host,
        port=port,
        addresses=addresses,
    )


class _GuardedTransport(httpx.AsyncHTTPTransport):
    """Transport that refuses to connect to a non-public address.

    Every request — including each redirect hop — is re-validated through
    :func:`resolve_and_validate_url`. Validation happens before the underlying
    transport opens a socket, so a redirect from a public host to
    ``169.254.169.254`` is rejected rather than followed.
    """

    def __init__(self, *, allow_private_network: bool = False) -> None:
        super().__init__()
        self._allow_private_network = allow_private_network

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resolve_and_validate_url(
            str(request.url), allow_private_network=self._allow_private_network
        )
        return await super().handle_async_request(request)


def guarded_async_client(
    *, timeout: float = 15.0, allow_private_network: bool = False
) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` that enforces this guard.

    ``follow_redirects`` is enabled because providers legitimately redirect
    (e.g. ``/v1`` → ``/v1/``); each hop is checked by ``_GuardedTransport``.
    ``allow_private_network`` has the meaning described on
    :func:`resolve_and_validate_url` and applies to every hop.
    """
    return httpx.AsyncClient(
        timeout=timeout,
        transport=_GuardedTransport(allow_private_network=allow_private_network),
        follow_redirects=True,
    )
