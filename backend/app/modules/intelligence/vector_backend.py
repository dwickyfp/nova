"""StarRocks-specific projection and retrieval boundary for Nova AI Search."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

from app.core.database import db

_INDEX_ID = re.compile(r"^[0-9a-f]{32}$")
_DATABASE = "_NOVA_AI_SEARCH"


class VectorBackendError(ValueError):
    """A requested vector configuration is unsupported or unavailable."""


@dataclass(frozen=True)
class SearchProjection:
    index_id: str
    version: int
    dimensions: int
    metric: Literal["cosine", "l2"]
    lexical_only: bool = False

    def __post_init__(self) -> None:
        if self.metric not in {"cosine", "l2"}:
            raise VectorBackendError("Unsupported vector metric")

    def table(self) -> str:
        compact = self.index_id.replace("-", "").lower()
        if not _INDEX_ID.fullmatch(compact) or self.version < 1:
            raise VectorBackendError("Invalid search projection identity")
        return f"`{_DATABASE}`.`IDX_{compact}_V{self.version}`"


class StarRocksVectorBackend:
    """Create isolated version tables and query them through StarRocks indexes."""

    async def health(self) -> dict[str, bool]:
        async def enabled(name: str) -> bool:
            result = await db.execute_system(f'ADMIN SHOW FRONTEND CONFIG LIKE "{name}"')
            return bool(result["rows"]) and str(result["rows"][0][2]).lower() == "true"

        return {
            "vector": await enabled("enable_experimental_vector"),
            "full_text": await enabled("enable_experimental_gin"),
        }

    async def create_index(self, projection: SearchProjection) -> None:
        if not projection.lexical_only and not 1 <= projection.dimensions <= 4096:
            raise VectorBackendError("Embedding dimensions must be between 1 and 4096")
        table = projection.table()
        capabilities = await self.health()
        if not capabilities["full_text"] or (
            not projection.lexical_only and not capabilities["vector"]
        ):
            raise VectorBackendError(
                "Required StarRocks vector and full-text indexing must be enabled "
                "on a shared-nothing cluster"
            )
        metric = "cosine_similarity" if projection.metric == "cosine" else "l2_distance"
        vector_index = (
            ""
            if projection.lexical_only
            else "INDEX idx_vector (embedding) USING VECTOR ("
            '"index_type"="hnsw", '
            f'"metric_type"="{metric}", "dim"="{projection.dimensions}", "M"="16"), '
        )
        embedding_column = (
            "embedding ARRAY<FLOAT>, "
            if projection.lexical_only
            else "embedding ARRAY<FLOAT> NOT NULL, "
        )
        await db.execute_system(f"CREATE DATABASE IF NOT EXISTS `{_DATABASE}`")
        try:
            await db.execute_system(
                f"CREATE TABLE {table} ("
                "source_key VARCHAR(512) NOT NULL, content STRING NOT NULL, metadata JSON, "
                f"{embedding_column}content_hash VARCHAR(64) NOT NULL, "
                "model_id VARCHAR(64), model_revision VARCHAR(128), "
                "indexed_at DATETIME NOT NULL, "
                f"{vector_index}"
                'INDEX idx_content (content) USING GIN ("parser"="english", '
                '"imp_lib"="builtin")) '
                "DUPLICATE KEY(source_key) DISTRIBUTED BY HASH(source_key) BUCKETS 1 "
                'PROPERTIES("replication_num"="1")'
            )
        except Exception as exc:
            raise VectorBackendError(
                "StarRocks could not create the requested search projection"
            ) from exc

    async def drop_index(self, projection: SearchProjection) -> None:
        await db.execute_system(f"DROP TABLE IF EXISTS {projection.table()}")

    @staticmethod
    def vector_sql(projection: SearchProjection, vector: list[float], top_k: int) -> str:
        if projection.lexical_only:
            raise VectorBackendError("This search projection has no vector index")
        if len(vector) != projection.dimensions or not 1 <= top_k <= 1000:
            raise VectorBackendError("Query vector dimensions or top_k are invalid")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        ):
            raise VectorBackendError("Query vector contains invalid values")
        values = ",".join(str(float(value)) for value in vector)
        function = (
            "approx_cosine_similarity" if projection.metric == "cosine" else "approx_l2_distance"
        )
        direction = "DESC" if projection.metric == "cosine" else "ASC"
        distance = f"{function}(embedding, [{values}])"
        return (
            f"SELECT source_key, content, metadata, {distance} AS score "
            f"FROM {projection.table()} ORDER BY {distance} {direction} LIMIT {top_k}"
        )

    async def search(self, projection: SearchProjection, vector: list[float], top_k: int) -> dict:
        return await db.execute_system(self.vector_sql(projection, vector, top_k))

    async def explain(self, projection: SearchProjection, vector: list[float], top_k: int) -> dict:
        return await db.execute_system("EXPLAIN " + self.vector_sql(projection, vector, top_k))


vector_backend = StarRocksVectorBackend()
