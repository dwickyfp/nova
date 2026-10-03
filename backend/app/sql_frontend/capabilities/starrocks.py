from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, fields, replace


@dataclass(frozen=True, slots=True)
class EngineVersion:
    major: int
    minor: int
    patch: int
    prerelease: str | None = None

    @property
    def release(self) -> tuple[int, int, int]:
        return self.major, self.minor, self.patch

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base + (f"-{self.prerelease}" if self.prerelease else "")


@dataclass(frozen=True, slots=True)
class EngineIdentity:
    product: str = "starrocks"
    version: EngineVersion | None = None
    distribution: str | None = None
    build: str | None = None
    deployment_mode: str | None = None
    feature_overrides: tuple[tuple[str, bool], ...] = ()


def normalize_identity(raw: str, *, deployment_mode: str | None = None) -> EngineIdentity:
    match = re.fullmatch(
        r"(?:StarRocks(?: version)?\s+)?(\d+)\.(\d+)\.(\d+)"
        r"(?:-([A-Za-z0-9][A-Za-z0-9.-]*))?(?:[+ ]([A-Za-z0-9][A-Za-z0-9._-]*))?",
        raw.strip(),
        flags=re.IGNORECASE,
    )
    if match is None:
        return EngineIdentity(deployment_mode=deployment_mode)
    major, minor, patch, prerelease, build = match.groups()
    if prerelease and re.fullmatch(r"[0-9a-fA-F]{7,40}", prerelease):
        build, prerelease = prerelease, None
    return EngineIdentity(
        version=EngineVersion(int(major), int(minor), int(patch), prerelease),
        build=build,
        deployment_mode=deployment_mode,
    )


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    engine: str = "starrocks"
    version: str = "4.1.4"
    native_merge: bool = False
    merge_all_by_name: bool = False
    merge_schema_evolution: bool = False
    sql_transactions: bool = False
    transaction_update_delete: bool = False
    transaction_repeated_insert: bool = False
    describe_files: bool = False
    identity: EngineIdentity | None = None
    source: str = "conservative"
    query_profiles: bool = False
    plan_advisor: bool = False
    sql_plan_manager: bool = False
    be_logs: bool = False

    def __post_init__(self) -> None:
        if self.identity is None:
            object.__setattr__(self, "identity", normalize_identity(self.version))


FEATURES = frozenset(
    {
        "native_merge",
        "merge_all_by_name",
        "merge_schema_evolution",
        "sql_transactions",
        "transaction_update_delete",
        "transaction_repeated_insert",
        "describe_files",
        "query_profiles",
        "plan_advisor",
        "sql_plan_manager",
        "be_logs",
    }
)


def validate_overrides(overrides: Mapping[str, bool]) -> None:
    if set(overrides) - FEATURES or any(type(value) is not bool for value in overrides.values()):
        raise ValueError("Engine overrides require known features and boolean values")


@dataclass(slots=True)
class _CacheEntry:
    capabilities: EngineCapabilities
    expires: float


class StarRocksCapabilityProvider:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._profiles = {"4.1.4": EngineCapabilities()}
        self._detected: dict[tuple, _CacheEntry] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}
        self._generation = 0
        self._clock = clock

    def register(self, capabilities: EngineCapabilities) -> None:
        if capabilities.version in self._profiles:
            raise ValueError("Capability profile already registered")
        self._profiles[capabilities.version] = capabilities
        self.invalidate()

    def for_version(self, version: str = "4.1.4") -> EngineCapabilities:
        return self._profiles.get(version, EngineCapabilities(version=version))

    def resolve(
        self, identity: EngineIdentity, overrides: Mapping[str, bool] | None = None
    ) -> EngineCapabilities:
        overrides = overrides or {}
        validate_overrides(overrides)
        version = identity.version
        stable = version is not None and version.prerelease is None
        release = version.release if version else (0, 0, 0)
        shared = identity.deployment_mode == "shared_data"
        base = self.for_version(str(version) if version else "unknown")
        base = replace(
            base,
            identity=replace(identity, feature_overrides=tuple(sorted(overrides.items()))),
            sql_transactions=stable and release >= (3, 5, 0),
            transaction_update_delete=stable and shared and release >= (4, 0, 0),
            transaction_repeated_insert=stable and shared and release >= (4, 0, 0),
            describe_files=stable and release >= (3, 3, 4),
            query_profiles=stable and release >= (4, 1, 4),
            plan_advisor=stable and release >= (4, 1, 4),
            sql_plan_manager=stable and release >= (4, 1, 4),
            be_logs=stable and release >= (4, 1, 4),
            source="operator_override"
            if overrides
            else "version_profile"
            if stable
            else "conservative",
        )
        return EngineCapabilities(
            **{
                item.name: overrides.get(item.name, getattr(base, item.name))
                for item in fields(base)
            }
        )

    def invalidate(self, engine_target: str | None = None) -> None:
        self._generation += 1
        if engine_target is None:
            self._detected.clear()
        else:
            self._detected = {
                key: value for key, value in self._detected.items() if key[0] != engine_target
            }

    async def detect(
        self,
        engine_target: str,
        version_reader: Callable[[], Awaitable[str]],
        *,
        overrides: Mapping[str, bool] | None = None,
        deployment_mode: str | None = None,
        mode_reader: Callable[[], Awaitable[str | None]] | None = None,
        config_identity: str | None = None,
    ) -> EngineCapabilities:
        overrides = overrides or {}
        validate_overrides(overrides)
        key = (engine_target, config_identity, deployment_mode, tuple(sorted(overrides.items())))
        cached = self._detected.get(key)
        if cached is not None and cached.expires > self._clock():
            return cached.capabilities
        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._detected.get(key)
            if cached is not None and cached.expires > self._clock():
                return cached.capabilities
            generation = self._generation
            deadline = asyncio.get_running_loop().time() + 2
            try:
                raw = await asyncio.wait_for(version_reader(), timeout=2)
                identity = normalize_identity(raw, deployment_mode=deployment_mode)
            except Exception:
                identity = EngineIdentity(deployment_mode=deployment_mode)
            if identity.deployment_mode is None and mode_reader and identity.version:
                try:
                    mode = await asyncio.wait_for(
                        mode_reader(), timeout=max(0, deadline - asyncio.get_running_loop().time())
                    )
                    if mode in {"shared_data", "shared_nothing"}:
                        identity = replace(identity, deployment_mode=mode)
                except Exception:
                    pass
            capabilities = self.resolve(identity, overrides)
            if generation == self._generation:
                self._detected[key] = _CacheEntry(
                    capabilities, self._clock() + (900 if identity.version else 30)
                )
            return capabilities


capability_provider = StarRocksCapabilityProvider()


async def resolve_engine_capabilities() -> EngineCapabilities:
    from app.core.config import load_nova_app_config, settings
    from app.core.database import db

    config = load_nova_app_config().engine

    async def read_version() -> str:
        result = await db.execute_system("SELECT CURRENT_VERSION()")
        return str(result["rows"][0][0])

    async def read_mode() -> str | None:
        result = await db.execute_system("ADMIN SHOW FRONTEND CONFIG LIKE 'run_mode'")
        names = [str(name).casefold() for name in result.get("columns", [])]
        if result["rows"] and "value" in names:
            return str(result["rows"][0][names.index("value")])
        return None

    target = f"{settings.STARROCKS_HOST}:{settings.STARROCKS_FE_MYSQL_PORT}"
    return await capability_provider.detect(
        target,
        read_version,
        overrides=dict(config.feature_overrides),
        deployment_mode=config.deployment_mode,
        mode_reader=read_mode,
        config_identity=settings.NOVA_CONFIG_PATH,
    )
