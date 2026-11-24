"""Native document storage within the existing actor-bound metadata transaction."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from psycopg.types.json import Jsonb

from app.core.exceptions import NovaException
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.workspaces.metadata_sharing import WorkspaceMetadataScope

from .bindings import (
    document_bindings,
    validate_chart_binding,
    validate_control_binding,
    validate_document_controls,
    validate_relationship_binding,
    validate_widget_bindings,
)
from .canvas import current as current_dashboard
from .canvas import dashboard_definition
from .contracts.authoring import DashboardDatasetCreate, DatasetCreate, DatasetUpdate
from .contracts.charts import ChartDefinition
from .contracts.commands import (
    DeleteRequest,
    GrantsReplace,
    Operation,
    PublicationRequest,
    RestoreRequest,
)
from .contracts.common import Contract, Permission
from .contracts.dashboards import DashboardDefinition, DatasetReference
from .contracts.dashboards_v2 import DashboardDefinitionV2
from .contracts.datasets import DatasetDefinition, LocalMetricSource
from .operations import apply_operation

Document = DatasetDefinition | ChartDefinition | DashboardDefinition
#: What a create or replace request may carry; a v2 dashboard is upcast before it is stored.
Submitted = Document | DashboardDefinitionV2
Kind = Literal["dataset", "chart", "dashboard"]


def dataset_intent(
    request: DatasetUpdate, *, workspace_id: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    if (workspace_id is None) == (expected_revision is None):
        raise ValueError("Dataset intent requires either workspace or prior revision")
    return {
        "command": "dataset.create" if workspace_id is not None else "dataset.replace",
        "workspace_id": workspace_id,
        "expected_revision": expected_revision,
        "request": request.model_dump(mode="json", exclude_defaults=True),
    }


def canonical(value: Contract | dict[str, Any]) -> bytes:
    data = value.model_dump(mode="json") if isinstance(value, Contract) else value
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 1_048_576:
        raise ValueError("Document exceeds the one MiB limit")
    return encoded


def dashboard_dataset_intent(
    identity: UUID, request: DashboardDatasetCreate, expected_revision: int,
) -> dict[str, Any]:
    return {"command": "dashboard.dataset.create", "dashboard_id": str(identity),
            "expected_revision": expected_revision,
            "request": request.model_dump(mode="json", exclude_defaults=True)}


def document_identity(document: Submitted) -> tuple[Kind, UUID]:
    if isinstance(document, DatasetDefinition):
        return "dataset", document.dataset_id
    if isinstance(document, ChartDefinition):
        return "chart", document.chart_id
    return "dashboard", document.dashboard_id


def parse_document(kind: str, payload: dict[str, Any]) -> Document:
    canonical(payload)
    if kind == "dataset":
        return DatasetDefinition.model_validate(payload)
    if kind == "chart":
        return ChartDefinition.model_validate(payload)
    if kind == "dashboard":
        return dashboard_definition(payload)
    raise ValueError("Unknown native document kind")


def _current(document: Submitted) -> Document:
    """The stored form: the digest keeps the submitted shape so old receipts replay."""
    if isinstance(document, DashboardDefinitionV2):
        return current_dashboard(document)
    return document


class VizDocumentRepository(WorkspaceMetadataScope):
    """No connection owner, commit, external data query or retry lives here."""

    async def _permission(self, identity: UUID, permission: Permission) -> None:
        await self._transaction()
        cursor = await self.connection.execute(
            "SELECT nova_meta.viz_can(%s,%s) AS allowed", (identity, permission),
        )
        row = await cursor.fetchone()
        if not row or row["allowed"] is not True:
            raise MetadataPermissionDenied()

    async def _head(self, identity: UUID, *, lock: bool = False) -> dict[str, Any]:
        await self._transaction()
        if lock:
            await self._head(identity)
            await self._permission(identity, "edit")
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.viz_resources WHERE resource_id=%s"
            + (" FOR UPDATE" if lock else ""), (identity,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise NovaException("Document not found", status_code=404)
        return row

    async def read(
        self, identity: UUID, *, draft: bool = False, revision: int | None = None,
    ) -> dict[str, Any]:
        if revision is not None and (
            type(revision) is not int or not 1 <= revision <= 9_007_199_254_740_991
        ):
            raise NovaException("Invalid document revision", status_code=422)
        head = await self._head(identity)
        if revision is not None and not draft:
            # A viewer reads the exact revision a publication pins. Revision row
            # security admits it only through a visible current publication.
            document = await self._definition(identity, revision, head["kind"])
            return {"resource_id": identity, "revision": revision,
                    "metadata_revision": head["metadata_revision"],
                    "grants_revision": head["grants_revision"],
                    "publication_id": head["current_publication_id"], "definition": document}
        if draft:
            await self._permission(identity, "edit")
            revision = head["draft_revision"] if revision is None else revision
        else:
            cursor = await self.connection.execute(
                "SELECT revision FROM nova_meta.viz_publications WHERE publication_id=%s",
                (head["current_publication_id"],),
            )
            publication = await cursor.fetchone()
            if publication is None:
                raise NovaException("This document has not been published", status_code=404)
            revision = publication["revision"]
        document = await self._definition(identity, revision, head["kind"])
        return {"resource_id": identity, "revision": revision,
                "metadata_revision": head["metadata_revision"],
                "grants_revision": head["grants_revision"],
                "publication_id": head["current_publication_id"], "definition": document}

    async def head(self, identity: UUID) -> dict[str, Any]:
        """The visible resource row (kind, workspace, revisions); row security decides."""
        return await self._head(identity)

    async def require_kind(self, identity: UUID, kind: Kind) -> None:
        if (await self._head(identity))["kind"] != kind:
            raise NovaException("Document not found", status_code=404)

    async def dashboard_datasets(self, identity: UUID, revision: int) -> dict[str, Any]:
        snapshot = await self.read(identity, draft=True, revision=revision)
        document = snapshot["definition"]
        if not isinstance(document, DashboardDefinition):
            raise NovaException("Document not found", status_code=404)
        references = document.datasets
        if not references:
            return {"dashboard_id": identity, "revision": revision, "datasets": []}
        cursor = await self.connection.execute(
            "SELECT pin.dataset_id,pin.revision,v.definition->>'title' AS title "
            "FROM unnest(%s,%s) WITH ORDINALITY "
            "AS pin(dataset_id,revision,position) "
            "LEFT JOIN nova_meta.viz_resources r ON r.resource_id=pin.dataset_id "
            "AND r.kind='dataset' AND r.deleted_at IS NULL "
            "AND nova_meta.viz_can(r.resource_id,'edit') "
            "LEFT JOIN nova_meta.viz_revisions v ON v.resource_id=r.resource_id "
            "AND v.revision=pin.revision ORDER BY pin.position",
            ([item.dataset_id for item in references], [item.revision for item in references]),
        )
        return {"dashboard_id": identity, "revision": revision,
                "datasets": await cursor.fetchall()}

    @staticmethod
    def _page_limit(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Page size must be between one and 100")

    async def browse(
        self, workspace_id: str, *, kind: Kind = "dashboard", draft: bool = False,
        before: tuple[datetime, UUID] | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Keyset pages project only the authorized draft or publication title."""
        await self._transaction()
        self._page_limit(limit)
        if kind not in {"dataset", "chart", "dashboard"} or type(draft) is not bool:
            raise ValueError("Invalid document listing")
        if before is not None and before[0].utcoffset() is None:
            raise ValueError("Document cursor requires an explicit timezone")
        cursor_filter = " AND (r.updated_at,r.resource_id)<(%s,%s)" if before else ""
        parameters: tuple[Any, ...] = (draft, workspace_id, kind, draft)
        if before is not None:
            parameters += before
        cursor = await self.connection.execute(
            "SELECT r.resource_id,r.kind,r.owner_name,r.workspace_id,r.updated_at,"
            "r.metadata_revision,r.grants_revision,r.current_publication_id,"
            "v.revision,v.definition->>'title' AS title "
            "FROM nova_meta.viz_resources r "
            "LEFT JOIN nova_meta.viz_publications p ON p.publication_id=r.current_publication_id "
            "JOIN nova_meta.viz_revisions v ON v.resource_id=r.resource_id AND "
            "v.revision=CASE WHEN %s THEN r.draft_revision ELSE p.revision END "
            "WHERE r.workspace_id=%s AND r.kind=%s AND r.deleted_at IS NULL "
            "AND (NOT %s OR nova_meta.viz_can(r.resource_id,'edit'))"
            + cursor_filter + " ORDER BY r.updated_at DESC,r.resource_id DESC LIMIT %s",
            (*parameters, limit),
        )
        return await cursor.fetchall()

    async def history(
        self, identity: UUID, *, before: int | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        await self._head(identity)
        await self._permission(identity, "edit")
        self._page_limit(limit)
        if before is not None and (type(before) is not int or not 1 <= before <= 2**53 - 1):
            raise ValueError("Invalid revision cursor")
        cursor_filter = " AND revision<%s" if before is not None else ""
        parameters = (identity, before, limit) if before is not None else (identity, limit)
        cursor = await self.connection.execute(
            "SELECT revision,schema_version,digest,author_name,created_at "
            "FROM nova_meta.viz_revisions WHERE resource_id=%s"
            + cursor_filter + " ORDER BY revision DESC LIMIT %s", parameters,
        )
        return await cursor.fetchall()

    async def revision(self, identity: UUID, revision: int) -> Document:
        head = await self._head(identity)
        if type(revision) is not int or not 1 <= revision <= 2**53 - 1:
            raise ValueError("Invalid document revision")
        # Revision RLS admits editors and the exact versions pinned by a current
        # publication visible to this actor. A resource grant alone is not enough.
        return await self._definition(identity, revision, head["kind"])

    _COMMENT_COLUMNS = ("comment_id,resource_id,page_id,widget_id,author,body,created_at,"
                        "resolved_at,resolved_by,"
                        "author=current_setting('nova.principal',true) AS mine")

    async def comments(self, identity: UUID) -> list[dict[str, Any]]:
        """Comments on a document the caller can view, oldest first."""
        await self._head(identity)
        await self._permission(identity, "view")
        cursor = await self.connection.execute(
            f"SELECT {self._COMMENT_COLUMNS} FROM nova_meta.viz_comments "
            "WHERE resource_id=%s AND deleted_at IS NULL ORDER BY created_at,comment_id LIMIT 500",
            (identity,),
        )
        return list(await cursor.fetchall())

    async def add_comment(self, identity: UUID, comment_id: UUID, body: str, *,
                          page_id: UUID | None, widget_id: UUID | None) -> dict[str, Any]:
        await self._head(identity)
        await self._permission(identity, "comment")
        # A retried post with the same id returns the comment it already saved.
        await self.connection.execute(
            "INSERT INTO nova_meta.viz_comments "
            "(comment_id,resource_id,page_id,widget_id,author,body) "
            "VALUES (%s,%s,%s,%s,current_setting('nova.principal',true),%s) "
            "ON CONFLICT (comment_id) DO NOTHING", (comment_id, identity, page_id, widget_id, body),
        )
        return await self._comment(identity, comment_id)

    async def _comment(self, identity: UUID, comment_id: UUID) -> dict[str, Any]:
        cursor = await self.connection.execute(
            f"SELECT {self._COMMENT_COLUMNS} FROM nova_meta.viz_comments "
            "WHERE resource_id=%s AND comment_id=%s AND deleted_at IS NULL", (identity, comment_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise NovaException("Comment not found", status_code=404)
        return dict(row)

    async def resolve_comment(self, identity: UUID, comment_id: UUID,
                              resolved: bool) -> dict[str, Any]:
        await self._head(identity)
        await self._comment(identity, comment_id)
        await self.connection.execute(
            "UPDATE nova_meta.viz_comments SET resolved_by=CASE WHEN %s "
            "THEN current_setting('nova.principal',true) END,"
            "resolved_at=CASE WHEN %s THEN clock_timestamp() END "
            "WHERE resource_id=%s AND comment_id=%s", (resolved, resolved, identity, comment_id),
        )
        return await self._comment(identity, comment_id)

    async def delete_comment(self, identity: UUID, comment_id: UUID) -> None:
        await self._head(identity)
        await self._comment(identity, comment_id)
        await self.connection.execute(
            "UPDATE nova_meta.viz_comments SET deleted_at=clock_timestamp() "
            "WHERE resource_id=%s AND comment_id=%s", (identity, comment_id),
        )

    async def permissions(self, identity: UUID) -> list[str]:
        await self._head(identity)
        cursor = await self.connection.execute(
            "SELECT action FROM unnest(ARRAY['view','interact','comment','edit',"
            "'publish','manage','export']) AS actions(action) "
            "WHERE nova_meta.viz_can(%s,action)", (identity,),
        )
        return [row["action"] for row in await cursor.fetchall()]

    async def grants(self, identity: UUID) -> list[dict[str, Any]]:
        await self._head(identity)
        await self._permission(identity, "manage")
        cursor = await self.connection.execute(
            "SELECT subject_type,subject_id,permission,expires_at,granted_by,granted_at "
            "FROM nova_meta.viz_acl_grants WHERE resource_id=%s "
            "ORDER BY subject_type,subject_id,permission", (identity,),
        )
        return await cursor.fetchall()

    async def grant_state(self, identity: UUID) -> dict[str, Any]:
        await self._permission(identity, "manage")
        # The shared lock keeps the revision and ACL in one committed state.
        cursor = await self.connection.execute(
            "SELECT grants_revision FROM nova_meta.viz_resources "
            "WHERE resource_id=%s FOR SHARE", (identity,),
        )
        head = await cursor.fetchone()
        if head is None:
            raise NovaException("Document not found", status_code=404)
        return {"revision": head["grants_revision"], "grants": await self.grants(identity)}

    async def receipt(self, identity: UUID | None, operation_id: UUID) -> dict[str, Any]:
        """Resolve an uncertain acknowledgement without replaying its mutation."""
        await self._transaction()
        resource_filter = " AND resource_id=%s" if identity is not None else ""
        parameters: tuple[Any, ...] = (
            operation_id, self.actor.principal, self.actor.active_role,
            self.actor.session_id, self.actor.security_context_version,
        )
        if identity is not None:
            parameters += (identity,)
        cursor = await self.connection.execute(
            "SELECT operation_id,resource_id,base_revision,new_revision,event_sequence,"
            "committed_at FROM nova_meta.viz_operations WHERE operation_id=%s "
            "AND actor_name=%s AND actor_role=%s AND session_id=%s AND security_context_version=%s"
            + resource_filter, parameters,
        )
        row = await cursor.fetchone()
        if row is None:
            raise NovaException("Operation receipt not found", status_code=404)
        return self._receipt(row)

    async def events(
        self, identity: UUID, *, after: int = 0, limit: int = 100,
    ) -> dict[str, Any]:
        """Replay ordered invalidations; definition/data payloads are never broadcast."""
        head = await self._head(identity)
        self._page_limit(limit)
        if type(after) is not int or after < 0:
            raise ValueError("Invalid event cursor")
        through = head["metadata_revision"]
        if after > through:
            raise MetadataConflict()
        cursor = await self.connection.execute(
            "SELECT event_id,operation_id,sequence,event_kind,created_at "
            "FROM nova_meta.viz_outbox WHERE resource_id=%s AND sequence>%s AND sequence<=%s "
            "ORDER BY sequence LIMIT %s", (identity, after, through, limit),
        )
        rows = await cursor.fetchall()
        return {"events": rows, "through": through,
                "next_sequence": rows[-1]["sequence"] if rows else after}

    async def _revision_payload(self, identity: UUID, revision: int) -> dict[str, Any]:
        cursor = await self.connection.execute(
            "SELECT definition,digest FROM nova_meta.viz_revisions WHERE resource_id=%s "
            "AND revision=%s", (identity, revision),
        )
        row = await cursor.fetchone()
        if row is None:
            raise NovaException("Document revision not found", status_code=404)
        if hashlib.sha256(canonical(row["definition"])).hexdigest() != row["digest"]:
            raise NovaException("Document integrity verification failed", status_code=503)
        return row["definition"]

    async def _definition(self, identity: UUID, revision: int, kind: str) -> Document:
        return parse_document(kind, await self._revision_payload(identity, revision))

    async def _checked_receipt(
        self, row: dict[str, Any], identity: UUID, digest: str, *, deleted_receipt: bool = False,
    ) -> dict[str, Any]:
        if (row["resource_id"] != identity or row["request_digest"] != digest
                or row["actor_name"] != self.actor.principal
                or row["actor_role"] != self.actor.active_role
                or row["session_id"] != self.actor.session_id
                or row["security_context_version"] != self.actor.security_context_version):
            raise MetadataConflict()
        if deleted_receipt:
            if row["kind"] != "delete":
                raise MetadataConflict()
        else:
            await self._permission(identity, "edit")
        return self._receipt(row)

    async def dataset_intent_receipt(
        self, request: DatasetUpdate, *, operation_id: UUID,
        workspace_id: str | None = None, expected_revision: int | None = None,
    ) -> dict[str, Any] | None:
        await self._transaction()
        payload = dataset_intent(request, workspace_id=workspace_id,
                                 expected_revision=expected_revision)
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.viz_operations WHERE operation_id=%s", (operation_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        receipt = await self._checked_receipt(row, request.dataset_id, row["request_digest"])
        if row["request_digest"] == hashlib.sha256(canonical(payload)).hexdigest():
            return receipt
        # Earlier authoring receipts hash the analyzed definition. Keep their
        # original validation path; receipt lookup itself never needs the engine.
        head = await self._head(request.dataset_id)
        if head["kind"] != "dataset":
            raise MetadataConflict()
        definition = await self._revision_payload(request.dataset_id, row["new_revision"])
        legacy = (
            {"command": "create", "workspace_id": workspace_id, "definition": definition}
            if workspace_id is not None
            else {"command": "replace", "expected_revision": expected_revision,
                  "definition": definition}
        )
        if row["request_digest"] != hashlib.sha256(canonical(legacy)).hexdigest():
            raise MetadataConflict()
        return None

    async def dashboard_dataset_receipt(
        self, identity: UUID, request: DashboardDatasetCreate, *, operation_id: UUID,
        expected_revision: int,
    ) -> dict[str, Any] | None:
        await self._transaction()
        payload = dashboard_dataset_intent(identity, request, expected_revision)
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.viz_operations WHERE operation_id=%s", (operation_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return await self._checked_receipt(
            row, identity, hashlib.sha256(canonical(payload)).hexdigest(),
        )

    async def _prior(
        self, operation_id: UUID, identity: UUID, payload: dict[str, Any],
        *, deleted_receipt: bool = False, legacy_payload: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        await self._transaction()
        digest = hashlib.sha256(canonical(payload)).hexdigest()
        await self.connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(operation_id),),
        )
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.viz_operations WHERE operation_id=%s", (operation_id,),
        )
        row = await cursor.fetchone()
        if row is not None:
            compared = digest
            if legacy_payload is not None and row["request_digest"] != digest:
                compared = hashlib.sha256(canonical(legacy_payload)).hexdigest()
            return digest, await self._checked_receipt(
                row, identity, compared, deleted_receipt=deleted_receipt,
            )
        return digest, None

    @staticmethod
    def _receipt(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row[key] for key in (
            "operation_id", "resource_id", "base_revision", "new_revision", "event_sequence",
            "committed_at",
        )}

    async def _record(
        self, operation_id: UUID, head: dict[str, Any], digest: str, kind: str,
        base_revision: int,
    ) -> dict[str, Any]:
        cursor = await self.connection.execute(
            "INSERT INTO nova_meta.viz_operations "
            "(operation_id,resource_id,request_digest,kind,base_revision,"
            "new_revision,event_sequence) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (operation_id, head["resource_id"], digest, kind, base_revision,
             head["draft_revision"], head["metadata_revision"]),
        )
        row = await cursor.fetchone()
        assert row is not None
        await self.connection.execute(
            "INSERT INTO nova_meta.viz_outbox "
            "(event_id,operation_id,resource_id,sequence,event_kind) VALUES (%s,%s,%s,%s,%s)",
            (uuid4(), operation_id, head["resource_id"], head["metadata_revision"], kind),
        )
        return self._receipt(row)

    async def _insert_revision(self, document: Document, revision: int) -> None:
        kind, identity = document_identity(document)
        document = parse_document(kind, document.model_dump(mode="json"))
        payload = canonical(document)
        await self.connection.execute(
            "INSERT INTO nova_meta.viz_revisions "
            "(resource_id,revision,schema_version,definition,digest) VALUES (%s,%s,%s,%s,%s)",
            (identity, revision, 3 if kind == "dashboard" else 2,
             Jsonb(json.loads(payload)), hashlib.sha256(payload).hexdigest()),
        )

    async def _references(
        self, document: Document, workspace: str,
    ) -> list[tuple[UUID, int]]:
        resolved: dict[tuple[UUID, int], Document] = {}
        closures: dict[tuple[UUID, int], tuple[frozenset[tuple[UUID, int]], int]] = {}
        closure_entries = 0

        async def visit(
            current_document: Document, ancestors: frozenset[UUID],
        ) -> tuple[frozenset[tuple[UUID, int]], int]:
            nonlocal closure_entries
            _, current = document_identity(current_document)
            if current in ancestors or len(ancestors) >= 16:
                raise ValueError("Dataset relationship graph is cyclic or too deep")
            ancestors = ancestors | {current}
            refs: set[tuple[UUID, int]] = set()
            requested: list[tuple[UUID, int, Kind]] = []
            if isinstance(current_document, ChartDefinition):
                requested.append((current_document.dataset_id,
                                  current_document.dataset_revision, "dataset"))
            elif isinstance(current_document, DatasetDefinition):
                requested.extend((relation.dataset_id, relation.dataset_revision, "dataset")
                                 for relation in current_document.relationships)
                if isinstance(current_document.source, LocalMetricSource):
                    requested.append((current_document.source.dataset_id,
                                      current_document.source.dataset_revision, "dataset"))
            elif isinstance(current_document, DashboardDefinition):
                requested.extend((item.dataset_id, item.revision, "dataset")
                                 for item in current_document.datasets)
                requested.extend((visual.chart_ref.chart_id, visual.chart_ref.revision, "chart")
                                 for _, visual in current_document.visuals())
            bindings = list(document_bindings(current_document))
            requested.extend((binding.dataset_id, binding.dataset_revision, "dataset")
                             for binding in bindings)
            height = 1
            for identity, revision, kind in dict.fromkeys(requested):
                key = (identity, revision)
                if identity in ancestors:
                    raise ValueError("Dataset relationship graph is cyclic or too deep")
                referenced = resolved.get(key)
                if referenced is None:
                    if len(resolved) >= 2048:
                        raise ValueError("Document exceeds the pinned reference limit")
                    head = await self._head(identity)
                    if head["workspace_id"] != workspace or head["kind"] != kind:
                        raise MetadataPermissionDenied()
                    referenced = await self._definition(identity, revision, kind)
                    resolved[key] = referenced
                elif document_identity(referenced)[0] != kind:
                    raise ValueError("Reference has an incompatible document kind")
                if isinstance(referenced, DatasetDefinition):
                    for binding in bindings:
                        if binding.dataset_id == identity and binding.dataset_revision == revision:
                            validate_control_binding(binding, referenced)
                if (isinstance(current_document, ChartDefinition)
                        and isinstance(referenced, DatasetDefinition)):
                    validate_chart_binding(current_document, referenced)
                if (isinstance(current_document, DatasetDefinition)
                        and isinstance(referenced, DatasetDefinition)):
                    for relation in current_document.relationships:
                        if (relation.dataset_id == identity
                                and relation.dataset_revision == revision):
                            validate_relationship_binding(current_document, relation, referenced)
                cached = closures.get(key)
                if cached is None:
                    cached = await visit(referenced, ancestors)
                    closure_entries += len(cached[0]) + 1
                    if closure_entries > 32768:
                        raise ValueError("Document exceeds the pinned reference expansion limit")
                    closures[key] = cached
                descendants, child_height = cached
                # A cached subtree can be reached under a different revision of
                # an ancestor. Recheck resource identities and the complete depth.
                if (any(item[0] in ancestors for item in descendants)
                        or len(ancestors) + child_height > 16):
                    raise ValueError("Dataset relationship graph is cyclic or too deep")
                refs.add(key)
                refs.update(descendants)
                height = max(height, child_height + 1)
            validate_document_controls(current_document, {
                key: value for key, value in resolved.items()
                if isinstance(value, DatasetDefinition)
            })
            if isinstance(current_document, DashboardDefinition):
                validate_widget_bindings(current_document, {
                    key: value for key, value in resolved.items()
                    if isinstance(value, ChartDefinition)
                })
            return frozenset(refs), height

        references, _ = await visit(document, frozenset())
        return sorted(references, key=lambda item: (str(item[0]), item[1]))

    async def create(
        self, workspace_id: str, document: Submitted, *, operation_id: UUID,
        intent: DatasetCreate | None = None,
    ) -> dict[str, Any]:
        kind, identity = document_identity(document)
        if intent is not None and (kind != "dataset" or intent.dataset_id != identity):
            raise ValueError("Dataset intent does not match the document")
        legacy = {
            "command": "create", "workspace_id": workspace_id,
            "definition": document.model_dump(mode="json"),
        }
        payload = (
            dataset_intent(intent, workspace_id=workspace_id) if intent is not None else legacy
        )
        digest, prior = await self._prior(operation_id, identity, payload,
                                         legacy_payload=legacy if intent is not None else None)
        if prior is not None:
            return prior
        return await self._create(workspace_id, _current(document), operation_id, digest)

    async def _create(
        self, workspace_id: str, document: Document, operation_id: UUID, digest: str,
    ) -> dict[str, Any]:
        kind, identity = document_identity(document)
        await self._references(document, workspace_id)
        cursor = await self.connection.execute(
            "INSERT INTO nova_meta.viz_resources(resource_id,workspace_id,kind) "
            "VALUES (%s,%s,%s) RETURNING *", (identity, workspace_id, kind),
        )
        head = await cursor.fetchone()
        assert head is not None
        await self._insert_revision(document, 1)
        return await self._record(operation_id, head, digest, "create", 0)

    async def create_dashboard_dataset(
        self, identity: UUID, request: DashboardDatasetCreate, document: DatasetDefinition, *,
        operation_id: UUID, expected_revision: int,
    ) -> dict[str, Any]:
        if document.dataset_id != request.dataset.dataset_id or document.dataset_id == identity:
            raise ValueError("Dataset intent does not match the document")
        payload = dashboard_dataset_intent(identity, request, expected_revision)
        digest, prior = await self._prior(operation_id, identity, payload)
        if prior is not None:
            return prior
        head = await self.require_dataset_creation(identity, expected_revision, lock=True)
        current = await self._definition(identity, expected_revision, "dashboard")
        assert isinstance(current, DashboardDefinition)
        reference = DatasetReference(dataset_id=document.dataset_id, revision=1)
        updated = DashboardDefinition.model_validate({
            **current.model_dump(mode="json"),
            "datasets": [*(item.model_dump(mode="json") for item in current.datasets),
                         reference.model_dump(mode="json")],
        })
        # The child and parent are committed together. A prior child operation
        # cannot be reused to turn an unrelated dataset into this creation.
        child = uuid5(operation_id, "dashboard.dataset.create")
        await self._create(head["workspace_id"], document, child, digest)
        await self._references(updated, head["workspace_id"])
        return await self._replace(head, updated, operation_id, digest)

    async def require_dataset_creation(
        self, identity: UUID, expected_revision: int, *, lock: bool = False,
    ) -> dict[str, Any]:
        head = await self._head(identity, lock=lock)
        await self._permission(identity, "edit")
        if head["kind"] != "dashboard":
            raise NovaException("Document not found", status_code=404)
        if head["draft_revision"] != expected_revision:
            raise MetadataConflict()
        cursor = await self.connection.execute(
            "SELECT nova_meta.workspace_namespace_permission(%s) AS permission",
            (head["workspace_namespace"],),
        )
        row = await cursor.fetchone()
        if row is None or row["permission"] not in {"owner", "edit"}:
            raise MetadataPermissionDenied()
        return head

    async def replace(
        self, document: Submitted, *, operation_id: UUID, expected_revision: int,
        intent: DatasetUpdate | None = None,
    ) -> dict[str, Any]:
        kind, identity = document_identity(document)
        if intent is not None and (kind != "dataset" or intent.dataset_id != identity):
            raise ValueError("Dataset intent does not match the document")
        legacy = {
            "command": "replace", "expected_revision": expected_revision,
            "definition": document.model_dump(mode="json"),
        }
        payload = dataset_intent(intent, expected_revision=expected_revision) if intent else legacy
        digest, prior = await self._prior(operation_id, identity, payload,
                                         legacy_payload=legacy if intent is not None else None)
        if prior is not None:
            return prior
        head = await self._head(identity, lock=True)
        await self._permission(identity, "edit")
        if head["draft_revision"] != expected_revision or head["kind"] != kind:
            raise MetadataConflict()
        stored = _current(document)
        await self._references(stored, head["workspace_id"])
        return await self._replace(head, stored, operation_id, digest)

    async def _replace(
        self, head: dict[str, Any], document: Document, operation_id: UUID, digest: str,
        *, kind: str = "edit",
    ) -> dict[str, Any]:
        base = head["draft_revision"]
        await self._insert_revision(document, base + 1)
        cursor = await self.connection.execute(
            "UPDATE nova_meta.viz_resources SET draft_revision=draft_revision+1,"
            "metadata_revision=metadata_revision+1 WHERE resource_id=%s AND draft_revision=%s "
            "RETURNING *", (head["resource_id"], base),
        )
        updated = await cursor.fetchone()
        if updated is None:
            raise MetadataConflict()
        return await self._record(operation_id, updated, digest, kind, base)

    async def apply(self, identity: UUID, operation: Operation) -> dict[str, Any]:
        digest, prior = await self._prior(operation.operation_id, identity, {
            "command": "apply", "resource_id": str(identity),
            "operation": operation.model_dump(mode="json"),
        })
        if prior is not None:
            return prior
        head = await self._head(identity, lock=True)
        await self._permission(identity, "edit")
        if head["draft_revision"] != operation.expected_revision:
            raise MetadataConflict()
        document = await self._definition(identity, head["draft_revision"], head["kind"])
        if not isinstance(document, DashboardDefinition):
            raise ValueError("Dashboard operations require a dashboard")
        updated = apply_operation(document, operation)
        await self._references(updated, head["workspace_id"])
        return await self._replace(head, updated, operation.operation_id, digest)

    async def publish(self, identity: UUID, request: PublicationRequest) -> dict[str, Any]:
        digest, prior = await self._prior(request.operation_id, identity, {
            "command": "publish", "resource_id": str(identity),
            "request": request.model_dump(mode="json"),
        })
        if prior is not None:
            return prior
        head = await self._head(identity, lock=True)
        await self._permission(identity, "publish")
        if head["draft_revision"] != request.expected_revision:
            raise MetadataConflict()
        document = await self._definition(identity, head["draft_revision"], head["kind"])
        references = await self._references(document, head["workspace_id"])
        publication_id = uuid4()
        await self.connection.execute(
            "INSERT INTO nova_meta.viz_publications"
            "(publication_id,resource_id,revision,release_notes)"
            " VALUES (%s,%s,%s,%s)",
            (publication_id, identity, head["draft_revision"], request.release_notes),
        )
        for reference, revision in references:
            await self.connection.execute(
                "INSERT INTO nova_meta.viz_publication_references"
                "(publication_id,resource_id,revision) VALUES (%s,%s,%s)",
                (publication_id, reference, revision),
            )
        cursor = await self.connection.execute(
            "UPDATE nova_meta.viz_resources SET current_publication_id=%s,"
            "metadata_revision=metadata_revision+1 WHERE resource_id=%s RETURNING *",
            (publication_id, identity),
        )
        updated = await cursor.fetchone()
        assert updated is not None
        return await self._record(request.operation_id, updated, digest, "publish",
                                  head["draft_revision"])

    async def restore(self, identity: UUID, request: RestoreRequest) -> dict[str, Any]:
        digest, prior = await self._prior(request.operation_id, identity, {
            "command": "restore", "resource_id": str(identity),
            "request": request.model_dump(mode="json"),
        })
        if prior is not None:
            return prior
        head = await self._head(identity, lock=True)
        await self._permission(identity, "edit")
        if head["draft_revision"] != request.expected_revision:
            raise MetadataConflict()
        cursor = await self.connection.execute(
            "SELECT revision FROM nova_meta.viz_publications WHERE publication_id=%s "
            "AND resource_id=%s", (request.publication_id, identity),
        )
        publication = await cursor.fetchone()
        if publication is None:
            raise NovaException("Publication not found", status_code=404)
        document = await self._definition(identity, publication["revision"], head["kind"])
        await self._references(document, head["workspace_id"])
        return await self._replace(head, document, request.operation_id, digest, kind="restore")

    async def replace_grants(self, identity: UUID, request: GrantsReplace) -> dict[str, Any]:
        """Directory validation of subjects belongs to the calling service."""
        digest, prior = await self._prior(request.operation_id, identity, {
            "command": "grants", "resource_id": str(identity),
            "request": request.model_dump(mode="json"),
        })
        if prior is not None:
            return prior
        head = await self._head(identity, lock=True)
        await self._permission(identity, "manage")
        if head["grants_revision"] != request.expected_revision:
            raise MetadataConflict()
        cursor = await self.connection.execute(
            "UPDATE nova_meta.viz_resources SET grants_revision=grants_revision+1,"
            "metadata_revision=metadata_revision+1 WHERE resource_id=%s RETURNING *", (identity,),
        )
        updated = await cursor.fetchone()
        assert updated is not None
        receipt = await self._record(request.operation_id, updated, digest, "grants",
                                     head["draft_revision"])
        rows = [
            {"subject_type": grant.subject_type, "subject_id": grant.subject_id,
             "permission": action, "expires_at": grant.model_dump(mode="json")["expires_at"]}
            for grant in request.grants for action in grant.permissions
        ]
        # One statement authorizes against the original ACL snapshot, including
        # when a manager removes their own grant. All effects still commit together.
        await self.connection.execute(
            "WITH removed AS (DELETE FROM nova_meta.viz_acl_grants WHERE resource_id=%s "
            "RETURNING resource_id) INSERT INTO nova_meta.viz_acl_grants "
            "(resource_id,subject_type,subject_id,permission,expires_at) "
            "SELECT %s,g.subject_type,g.subject_id,g.permission,g.expires_at "
            "FROM jsonb_to_recordset(%s) AS g(subject_type text,subject_id text,"
            "permission text,expires_at timestamptz) "
            "CROSS JOIN (SELECT count(*) FROM removed) AS completed",
            (identity, identity, Jsonb(rows)),
        )
        return receipt

    async def delete(
        self, identity: UUID, kind: Kind, request: DeleteRequest,
    ) -> dict[str, Any]:
        digest, prior = await self._prior(request.operation_id, identity, {
            "command": "delete", "resource_id": str(identity), "kind": kind,
            "request": request.model_dump(mode="json"),
        }, deleted_receipt=True)
        if prior is not None:
            return prior
        head = await self._head(identity, lock=True)
        if head["kind"] != kind:
            raise NovaException("Document not found", status_code=404)
        if head["owner_name"] != self.actor.principal:
            raise MetadataPermissionDenied()
        if head["draft_revision"] != request.expected_revision:
            raise MetadataConflict()
        updated = {**head, "metadata_revision": head["metadata_revision"] + 1}
        # The receipt is admitted while the owner can still read the document.
        # Deferred database constraints require the tombstone in this transaction.
        receipt = await self._record(request.operation_id, updated, digest, "delete",
                                     head["draft_revision"])
        cursor = await self.connection.execute(
            "UPDATE nova_meta.viz_resources SET deleted_at=clock_timestamp(),"
            "metadata_revision=metadata_revision+1 WHERE resource_id=%s AND draft_revision=%s",
            (identity, request.expected_revision),
        )
        if cursor.rowcount != 1:
            raise MetadataConflict()
        return receipt
