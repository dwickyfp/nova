from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    engine: str = "starrocks"
    version: str = "4.1.4"
    native_merge: bool = False
    merge_all_by_name: bool = False
    merge_schema_evolution: bool = False


class StarRocksCapabilityProvider:
    def __init__(self) -> None:
        self._profiles = {"4.1.4": EngineCapabilities()}
        self._detected: dict[str, EngineCapabilities] = {}

    def register(self, capabilities: EngineCapabilities) -> None:
        if capabilities.version in self._profiles:
            raise ValueError("Capability profile already registered")
        self._profiles[capabilities.version] = capabilities

    def for_version(self, version: str = "4.1.4") -> EngineCapabilities:
        return self._profiles.get(version, EngineCapabilities(version=version))

    async def detect(
        self, engine_target: str, version_reader: Callable[[], Awaitable[str]]
    ) -> EngineCapabilities:
        if engine_target not in self._detected:
            version = await version_reader()
            self._detected[engine_target] = self.for_version(version)
        return self._detected[engine_target]


capability_provider = StarRocksCapabilityProvider()
