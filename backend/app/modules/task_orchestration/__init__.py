"""Task orchestration module — Phase 9 metadata, repository, and DAG validation.

This layer sits *above* the native ``modules/tasks`` wrapper: it owns graph
definitions, edges, and run history in ``NOVA_SYSTEM``. It stores no
credentials — only the names of objects that hold them.
"""
