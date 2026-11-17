"""A Kafka cluster as a Nova connection for Routine Load.

A Kafka connection never becomes a catalog: ``CREATE ROUTINE LOAD ... FROM
KAFKA ("kafka_connection" = "<name>", ...)`` names it, and Nova supplies the
broker list and the security settings when the engine job is created
(:mod:`app.modules.routine_load`). The endpoint is

    host:port[,host:port...][?security_protocol=SASL_SSL&sasl_mechanism=SCRAM-SHA-512]

With a SASL protocol the connection credential is the SASL username and
password. ``PLAINTEXT`` and ``SSL`` connections take no credential; ``SSL``
verifies the brokers against the engine's trusted certificates.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar
from urllib.parse import parse_qsl

from ..errors import CatalogError
from .base import _HOST, CatalogSource, Credentials, Endpoint, ObjectKinds

PROTOCOLS = ("PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL")
MECHANISMS = ("PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512")
#: Most brokers one connection lists.
MAX_BROKERS = 32


class KafkaSource(CatalogSource):
    connector_type = "kafka"
    label = "Kafka"
    default_port = 9092
    endpoint_format = "host:port[,host:port...][?security_protocol=...&sasl_mechanism=...]"
    allowed_options: ClassVar[frozenset[str] | None] = frozenset(
        {"security_protocol", "sasl_mechanism"}
    )
    object_groups: ClassVar[tuple[str, ...]] = ()
    provides_catalog = False
    credential_label = "SASL username and password"

    def parse_endpoint(self, value: str) -> Endpoint:
        text = (value or "").strip()
        if not text or len(text) > 512:
            raise CatalogError(f"A Kafka endpoint is {self.endpoint_format}")
        if "@" in text or "://" in text or any(ch.isspace() for ch in text):
            raise CatalogError(
                f"A Kafka endpoint is {self.endpoint_format}; credentials and URL schemes "
                "are not accepted"
            )
        brokers_text, _, query = text.partition("?")
        brokers: list[str] = []
        first_port = 0
        for broker in brokers_text.split(","):
            host, _, port_text = broker.strip().partition(":")
            if not _HOST.match(host):
                raise CatalogError("A Kafka broker has an invalid host")
            try:
                port = int(port_text) if port_text else self.default_port
            except ValueError:
                raise CatalogError("A Kafka broker has an invalid port") from None
            if not 0 < port < 65536:
                raise CatalogError("A Kafka broker has an invalid port")
            address = f"{host}:{port}"
            if address not in brokers:
                brokers.append(address)
            first_port = first_port or port
        if not brokers or len(brokers) > MAX_BROKERS:
            raise CatalogError(f"A Kafka connection lists 1 to {MAX_BROKERS} brokers")
        options: dict[str, str] = {}
        for key, option in parse_qsl(query, keep_blank_values=True, strict_parsing=bool(query)):
            if key not in (self.allowed_options or ()) or key in options:
                raise CatalogError(f"Connection option {key!r} is not accepted")
            options[key] = option.strip().upper()
        protocol = options.setdefault("security_protocol", "PLAINTEXT")
        if protocol not in PROTOCOLS:
            raise CatalogError("security_protocol is one of " + ", ".join(PROTOCOLS))
        if protocol.startswith("SASL"):
            mechanism = options.setdefault("sasl_mechanism", "PLAIN")
            if mechanism not in MECHANISMS:
                raise CatalogError("sasl_mechanism is one of " + ", ".join(MECHANISMS))
        elif "sasl_mechanism" in options:
            raise CatalogError("sasl_mechanism applies to SASL_PLAINTEXT and SASL_SSL only")
        # The broker list is kept whole; ``port`` records the first broker's.
        return Endpoint(",".join(brokers), first_port, None, options)

    def requires_credentials(self, endpoint: Endpoint) -> bool:
        return endpoint.options.get("security_protocol", "PLAINTEXT").startswith("SASL")

    def routine_load_properties(
        self, endpoint: Endpoint, credentials: Credentials | None
    ) -> dict[str, str]:
        """``FROM KAFKA`` properties that reach the brokers. Carries the SASL password."""
        protocol = endpoint.options.get("security_protocol", "PLAINTEXT")
        properties = {"kafka_broker_list": endpoint.host}
        if protocol != "PLAINTEXT":
            properties["property.security.protocol"] = protocol
        if protocol.startswith("SASL"):
            if credentials is None:
                raise CatalogError("This Kafka connection needs its SASL credentials")
            properties["property.sasl.mechanism"] = endpoint.options.get("sasl_mechanism", "PLAIN")
            properties["property.sasl.username"] = credentials.username
            properties["property.sasl.password"] = credentials.password
        return properties

    async def read_objects(
        self, endpoint: Endpoint, credentials: Credentials, databases: Sequence[str]
    ) -> ObjectKinds:
        return {}
