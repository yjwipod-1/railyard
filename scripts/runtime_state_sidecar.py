"""
Runtime State Sidecar -- local OSS facade composing journal, projection, snapshot, and evidence-export APIs.
v0.8.0
"""

__version__ = "0.8.0"

import uuid as _uuid
from datetime import datetime, timezone as _timezone
from pathlib import Path as _Path

from scripts.runtime_state_journal import (
    RuntimeJournal,
    RuntimeJournalError,
    read_run_evidence_snapshot,
)
from scripts.runtime_state_projection import (
    ProjectionError,
    run_projection,
    run_projection_from_events,
    stage_projection,
)
from scripts.runtime_evidence_export import (
    RuntimeEvidenceExportError,
    export_run,
)


class RuntimeStateSidecarError(Exception):
    """Facade-owned errors: constructor validation, create_run enforcement, and closed-state guard."""
    def __init__(self, code: str, detail: dict | None = None):
        self.code = code
        self.detail = detail or {}
        super().__init__(f"RuntimeStateSidecarError({self.code}): {self.detail}")

    def __str__(self):
        return f"RuntimeStateSidecarError({self.code}): {self.detail}"


class RuntimeStateSidecar:
    """Local in-process facade that composes accepted journal, projection, snapshot, and evidence-export APIs.

    db_path and signer_key are mandatory. Default id_factory uses UUID4; default clock uses timezone-aware UTC.
    Injected id_factory and clock must be supported for deterministic tests.
    Never reads identity, time, key, or path from environment or workflow state.
    """

    def __init__(self, db_path: str, signer_key: bytes, *, id_factory=None, clock=None):
        # Validate db_path
        if not isinstance(db_path, str) or not db_path.strip():
            raise RuntimeStateSidecarError("invalid_db_path", {"db_path": db_path})
        self._db_path = str(_Path(db_path).resolve())
        # Validate signer_key
        if not isinstance(signer_key, bytes) or len(signer_key) == 0:
            raise RuntimeStateSidecarError("unsigned_signer_key", {})
        # id_factory: default UUID4
        if id_factory is None:
            import uuid
            id_factory = lambda: str(uuid.uuid4())
        self._id_factory = id_factory
        # clock: default timezone-aware UTC
        if clock is None:
            clock = lambda: datetime.now(_timezone.utc)
        self._clock = clock
        self._signer_key = signer_key
        self._journal = None
        self._closed = False
        self._open()

    def _open(self):
        self._journal = RuntimeJournal(self._db_path, self._signer_key)

    def _check_closed(self):
        if self._closed:
            raise RuntimeStateSidecarError("sidecar_closed", {})

    def close(self):
        if self._journal is not None and not self._closed:
            self._journal.__exit__(None, None, None)
        self._closed = True

    @property
    def closed(self):
        return self._closed

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # --- Run lifecycle ---

    def create_run(self, request: dict) -> dict:
        """Accept one complete AppendRequest. Requires event_type=run.created, expected_stream_head order 0 with ZERO_DIGEST, and prev_event_digest ZERO_DIGEST."""
        self._check_closed()
        from scripts.runtime_state_core import ZERO_DIGEST
        # Enforce genesis invariants BEFORE journal append
        if request.get("event_type") != "run.created":
            raise RuntimeStateSidecarError(
                "not_run_created",
                {"event_type": request.get("event_type")}
            )
        expected_head = request.get("expected_stream_head", {})
        if expected_head.get("event_order") != 0 or expected_head.get("content_digest") != ZERO_DIGEST:
            raise RuntimeStateSidecarError(
                "not_genesis_head",
                {"expected_stream_head": expected_head}
            )
        prev_digest = request.get("prev_event_digest")
        if prev_digest != ZERO_DIGEST:
            raise RuntimeStateSidecarError(
                "nonzero_prev_digest",
                {"prev_event_digest": prev_digest}
            )
        return self._journal.append(request)

    def append_event(self, request: dict) -> dict:
        """Forward the complete request unchanged. Delegate exactly once to RuntimeJournal.append."""
        self._check_closed()
        return self._journal.append(request)

    # --- Read ---

    def read_event(self, run_id: str, event_order: int):
        self._check_closed()
        return self._journal.read_event(run_id, event_order)

    def read_events(self, run_id: str):
        self._check_closed()
        return self._journal.read_events(run_id)

    # --- Projection ---

    def get_run(self, run_id: str):
        self._check_closed()
        return run_projection(run_id, self._db_path)

    def get_stage(self, run_id: str, stage_id: str):
        self._check_closed()
        return stage_projection(run_id, stage_id, self._db_path)

    # --- Snapshot ---

    def evidence_snapshot(self, run_id: str):
        self._check_closed()
        return read_run_evidence_snapshot(self._db_path, run_id)

    # --- Export ---

    def export_evidence(self, run_id: str, *, export_id=None, exported_at=None):
        """Generate omitted presentation identity/time; validate through accepted exporter; otherwise preserve explicit caller values."""
        self._check_closed()
        generated_export_id = export_id
        generated_exported_at = exported_at

        if generated_export_id is None:
            generated_export_id = f"export-{self._id_factory()}"
        if generated_exported_at is None:
            generated_exported_at = self._clock().isoformat()

        return export_run(
            self._db_path,
            run_id,
            export_id=generated_export_id,
            exported_at=generated_exported_at,
        )
