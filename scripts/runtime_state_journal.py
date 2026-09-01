"""SQLite runtime event journal using explicit-path storage and runtime_state_core.py.

Implements the Runtime State Contract v0.9.0 append protocol (Section 2.2) with
mandatory decision order: idempotency lookup, exact retry, divergent duplicate
rejection, stale-head check, hash-chain validation, request/payload/reducer
precondition checks, reducer execution, store-field assignment, digest
computation, receipt signing, and atomic commit.

v0.9.0: All decision reads and writes are serialized under BEGIN IMMEDIATE.
Concurrent same-head writers produce one receipt + one frozen stale-head.
Workflow database and .workflow paths are rejected before any mutation.
Explicit caller-supplied signing material is required.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone

from scripts.runtime_state_core import (
    ZERO_DIGEST,
    apply_reducer,
    bind_store_event_metadata,
    canonical_serialize,
    compute_event_digest,
    sign_receipt,
    validate_append_request,
    verify_stream_integrity,
)

_SCHEMA_VERSION = "0.9.0"

# Railyard workflow tables that must not exist in a runtime journal database.
_WORKFLOW_TABLES: set[str] = {
    "domain_epic",
    "domain_ticket",
    "system_epic",
    "system_ticket",
    "schema_version",
    "workflow_event",
}

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS runtime_events (
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_order INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    causation_id TEXT,
    causation_chain TEXT,
    actor_role TEXT NOT NULL,
    actor_identity TEXT NOT NULL,
    trigger_artifact TEXT NOT NULL,
    reason TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    expected_stream_head TEXT NOT NULL,
    client_event_id TEXT NOT NULL,
    prev_event_digest TEXT NOT NULL,
    prior_state TEXT NOT NULL,
    next_state TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    PRIMARY KEY (event_id),
    UNIQUE(run_id, event_order)
);

CREATE TABLE IF NOT EXISTS idempotency (
    client_event_id TEXT NOT NULL PRIMARY KEY,
    complete_request BLOB NOT NULL,
    stored_event_id TEXT NOT NULL,
    stored_receipt BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS stream_heads (
    run_id TEXT NOT NULL PRIMARY KEY,
    event_order INTEGER NOT NULL,
    content_digest TEXT NOT NULL,
    last_receipt BLOB
);
"""


def _request_canonical(request: dict) -> bytes:
    """Return canonical JCS bytes for a complete AppendRequest."""
    return canonical_serialize(request)


def _receipt_canonical(receipt: dict) -> bytes:
    """Return canonical JCS bytes for an AppendReceipt."""
    return canonical_serialize(receipt)


def _event_to_dict(row: tuple, columns: list[str]) -> dict:
    """Convert a database row to a RuntimeEvent dict, deserialising JSON fields."""
    d = dict(zip(columns, row))
    json_fields = [
        "payload", "causation_chain", "trigger_artifact",
        "expected_stream_head", "prior_state", "next_state",
    ]
    for field in json_fields:
        if field in d and d[field] is not None:
            d[field] = json.loads(d[field])
    # Ensure causation_chain is present as list or None
    if "causation_chain" in d:
        pass  # already parsed
    if d.get("causation_id") is not None and "causation_chain" in d:
        pass  # keep both
    return d


def _event_row_columns() -> list[str]:
    """Return ordered column list for runtime_events with all AppendRequest fields."""
    return [
        "run_id", "event_id", "event_order", "event_type",
        "payload", "causation_id", "causation_chain",
        "actor_role", "actor_identity", "trigger_artifact",
        "reason", "recommended_action", "expected_stream_head",
        "client_event_id", "prev_event_digest",
        "prior_state", "next_state", "occurred_at",
        "schema_version", "content_digest",
    ]


def _readonly_database_uri(path: pathlib.Path) -> str:
    """Return a SQLite URI that can only open an existing database read-only."""
    return f"{path.as_uri()}?mode=ro"


def _existing_store_preflight(path: pathlib.Path) -> None:
    """Reject an incompatible existing store without changing it in any way.

    This deliberately runs before the journal's writable connection is opened,
    before WAL is enabled, and before the runtime schema is created.  A zero-byte
    file is a valid fresh-store target and has no schema or rows to inspect.
    """
    if not path.exists() or path.stat().st_size == 0:
        return

    connection = sqlite3.connect(
        _readonly_database_uri(path), uri=True, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    try:
        existing = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        found = existing & _WORKFLOW_TABLES
        if found:
            raise RuntimeJournalError({
                "code": "workflow_schema_rejected",
                "tables": sorted(found),
            })
        if "runtime_events" in existing:
            _check_compatibility(connection)
    finally:
        connection.close()


def _check_compatibility(connection: sqlite3.Connection) -> None:
    """Require every stored RuntimeEvent to use exactly schema version 0.9.0."""
    rows = connection.execute(
        "SELECT DISTINCT schema_version FROM runtime_events"
    ).fetchall()
    versions = {row["schema_version"] for row in rows}
    if versions and versions != {_SCHEMA_VERSION}:
        raise RuntimeJournalError({"code": "unsupported_schema_version"})


class RuntimeJournalError(Exception):
    """Structured journal error with a code."""
    def __init__(self, detail: dict):
        self.detail = detail
        super().__init__(detail.get("code", "unknown"))

    def __str__(self):
        return json.dumps(self.detail)


class RuntimeJournal:
    """Explicit-path SQLite runtime event journal.

    All APIs require an explicit `db_path` and `signer_key`; there is no
    implicit default.  Paths under ``.workflow`` and databases containing
    Railyard workflow tables are rejected before any mutation.
    """

    def __init__(self, db_path: str, signer_key: bytes):
        # ---- Path isolation: reject .workflow paths (case-insensitive) ----
        resolved = pathlib.Path(db_path).resolve()
        for part in resolved.parts:
            if part.lower() == ".workflow":
                raise RuntimeJournalError({
                    "code": "workflow_path_rejected",
                    "path": str(resolved),
                })

        # ---- Signer key isolation: must be caller-supplied ----
        if not isinstance(signer_key, bytes) or len(signer_key) == 0:
            raise RuntimeJournalError({"code": "unsigned_receipt"})

        # Inspect existing stores through a read-only connection.  Incompatible
        # stores must be rejected before a writable open, WAL, schema creation,
        # or SQLite sidecar creation can occur.
        _existing_store_preflight(resolved)

        self.db_path = db_path
        self._signer_key = signer_key
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row

        # ---- Workflow schema isolation: reject before any mutation ----
        existing: set[str] = set()
        for row in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ):
            existing.add(row["name"])
        found = existing & _WORKFLOW_TABLES
        if found:
            self._conn.close()
            self._conn = None
            raise RuntimeJournalError({
                "code": "workflow_schema_rejected",
                "tables": sorted(found),
            })

        # WAL mode and schema creation only after all rejections pass
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_CREATE_TABLES)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        return False

    def _ensure_compatibility(self):
        """Check all runtime_events rows have schema_version exactly "0.9.0".

        Rejects with ``unsupported_schema_version`` if any row has a
        different version or if mixed versions exist.  Does NOT silently
        migrate, normalize, delete, rewrite, or append.
        """
        _check_compatibility(self._conn)

    # ------------------------------------------------------------------
    # Append (Section 2.2 mandatory decision order, serialized via BEGIN IMMEDIATE)
    # ------------------------------------------------------------------

    def append(self, request: dict) -> dict:
        """Append one RuntimeEvent following the mandatory decision order.

        The entire decision sequence (idempotency, head read, validation,
        reducer, store writes) is serialized under BEGIN IMMEDIATE so that
        concurrent same-head writers see the committed head and produce
        one receipt + one structured stale-head result.

        Returns an AppendReceipt dict on success.
        Raises RuntimeJournalError for divergent_duplicate or hash_chain_link.
        Returns structured error dict for stale_head.
        """
        # ---- Serialize the entire decision+writes under BEGIN IMMEDIATE ----
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            # Compatibility belongs to this exact write transaction and is the
            # final precondition immediately before the frozen append sequence.
            self._ensure_compatibility()

            # --- Step 1: Global idempotency lookup ---
            row = self._conn.execute(
                "SELECT complete_request, stored_event_id, stored_receipt "
                "FROM idempotency WHERE client_event_id = ?",
                (request["client_event_id"],),
            ).fetchone()

            if row is not None:
                stored_canonical = row["complete_request"]
                new_canonical = _request_canonical(request)
                # --- Step 2: Exact retry or divergent duplicate ---
                if stored_canonical == new_canonical:
                    # Exact duplicate: return original stored receipt byte-for-byte
                    receipt_bytes = row["stored_receipt"]
                    if isinstance(receipt_bytes, bytes):
                        receipt = json.loads(receipt_bytes.decode("utf-8"))
                    else:
                        receipt = json.loads(receipt_bytes)
                    self._conn.execute("COMMIT")
                    return receipt
                else:
                    # Divergent duplicate -- ROLLBACK handled by except
                    raise RuntimeJournalError({"code": "divergent_duplicate"})

            # --- Step 3: Read stream head (inside transaction, serialized) ---
            run_id = request["run_id"]
            head_row = self._conn.execute(
                "SELECT event_order, content_digest, last_receipt "
                "FROM stream_heads WHERE run_id = ?",
                (run_id,),
            ).fetchone()

            if head_row:
                current_head = {
                    "event_order": int(head_row["event_order"]),
                    "content_digest": head_row["content_digest"],
                }
                last_receipt = None
                if head_row["last_receipt"] is not None:
                    receipt_b = head_row["last_receipt"]
                    if isinstance(receipt_b, bytes):
                        last_receipt = json.loads(receipt_b.decode("utf-8"))
                    else:
                        last_receipt = receipt_b
            else:
                current_head = {"event_order": 0, "content_digest": ZERO_DIGEST}
                last_receipt = None

            # --- Step 4: Stale-head check ---
            if request["expected_stream_head"] != current_head:
                self._conn.execute("COMMIT")
                return {
                    "code": "stale_head",
                    "current_stream_head": current_head,
                    "last_stored_receipt": last_receipt,
                }

            # --- Step 5: Hash-chain link ---
            if request["prev_event_digest"] != current_head["content_digest"]:
                raise RuntimeJournalError({"code": "hash_chain_link"})

            # --- Step 6: Validate request shape, payload, reducer preconditions ---
            validation = validate_append_request(request)
            if not validation["valid"]:
                raise RuntimeJournalError({
                    "code": "invalid_request",
                    "errors": validation["errors"],
                    "rule_id": validation["rule_id"],
                })

            # --- Step 7: Execute reducer before any store writes ---
            prior_projection = None
            existing_rows = self._conn.execute(
                "SELECT * FROM runtime_events WHERE run_id = ? ORDER BY event_order",
                (run_id,),
            ).fetchall()

            if existing_rows:
                cols = _event_row_columns()
                last = _event_to_dict(tuple(existing_rows[-1]), cols)
                prior_state_raw = last.get("next_state", {})
                if isinstance(prior_state_raw, str):
                    prior_projection = json.loads(prior_state_raw)
                else:
                    prior_projection = prior_state_raw

            next_state = apply_reducer(
                prior_projection,
                {
                    "event_type": request["event_type"],
                    "payload": request["payload"],
                    "run_id": run_id,
                },
            )

            # --- Steps 8-10: Assign store fields, bind metadata, compute digest, sign receipt ---
            event_id = str(uuid.uuid4())
            event_order = current_head["event_order"] + 1
            occurred_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            schema_version = _SCHEMA_VERSION
            prior_state_store = prior_projection if prior_projection is not None else {}

            # Bind store-assigned event identity metadata onto the reducer
            # result before persistence. The reducer must not read or set this
            # metadata; this is the single shared helper used by both the
            # journal write-path and the projection replay path so they never
            # drift. prior_state is persisted as the preceding event's
            # metadata-complete next_state (byte-for-byte), satisfying the
            # contract that every stored next_state carries the complete
            # post-event projection including latest event identity metadata.
            next_state = bind_store_event_metadata(next_state, {
                "event_id": event_id,
                "event_type": request["event_type"],
                "event_order": event_order,
                "payload": request["payload"],
            })

            # Build the complete stored event dict for digest computation
            stored_event = _build_stored_event(
                request, event_id, event_order, occurred_at, schema_version,
                prior_state_store, next_state,
            )
            content_digest = compute_event_digest(stored_event)
            stored_event["content_digest"] = content_digest

            # Build receipt with explicit caller-supplied signer key
            signed = sign_receipt(run_id, event_order, content_digest,
                                  key_bytes=self._signer_key)
            receipt = {
                "event_id": event_id,
                "event_order": event_order,
                "stored_content_digest": content_digest,
                "new_stream_head": {
                    "event_order": event_order,
                    "content_digest": content_digest,
                },
                "signed_receipt": signed,
            }

            canonical_request = _request_canonical(request)
            canonical_receipt = _receipt_canonical(receipt)

            # --- Atomic writes inside the same transaction ---
            # Insert the event
            self._conn.execute(
                """INSERT INTO runtime_events
                   (run_id, event_id, event_order, event_type,
                    payload, causation_id, causation_chain,
                    actor_role, actor_identity, trigger_artifact,
                    reason, recommended_action, expected_stream_head,
                    client_event_id, prev_event_digest,
                    prior_state, next_state, occurred_at,
                    schema_version, content_digest)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    event_id,
                    event_order,
                    request["event_type"],
                    json.dumps(request["payload"]),
                    request.get("causation_id"),
                    json.dumps(request.get("causation_chain"))
                    if "causation_chain" in request else None,
                    request["actor_role"],
                    request["actor_identity"],
                    json.dumps(request["trigger_artifact"]),
                    request["reason"],
                    request["recommended_action"],
                    json.dumps(request["expected_stream_head"]),
                    request["client_event_id"],
                    request["prev_event_digest"],
                    json.dumps(prior_state_store),
                    json.dumps(next_state),
                    occurred_at,
                    schema_version,
                    content_digest,
                ),
            )

            # Upsert idempotency
            self._conn.execute(
                """INSERT INTO idempotency
                   (client_event_id, complete_request, stored_event_id, stored_receipt)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(client_event_id) DO UPDATE SET
                     complete_request = excluded.complete_request,
                     stored_event_id = excluded.stored_event_id,
                     stored_receipt = excluded.stored_receipt""",
                (request["client_event_id"], canonical_request, event_id,
                 canonical_receipt),
            )

            # Update stream head
            self._conn.execute(
                """INSERT INTO stream_heads
                   (run_id, event_order, content_digest, last_receipt)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(run_id) DO UPDATE SET
                     event_order = excluded.event_order,
                     content_digest = excluded.content_digest,
                     last_receipt = excluded.last_receipt""",
                (run_id, event_order, content_digest, canonical_receipt),
            )

            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        return receipt

    # ------------------------------------------------------------------
    # Read APIs
    # ------------------------------------------------------------------

    def read_event(self, run_id: str, event_order: int) -> dict | None:
        """Read a single event by run_id and event_order. Returns None if not found."""
        self._conn.execute("BEGIN")
        try:
            self._ensure_compatibility()
            row = self._conn.execute(
                "SELECT * FROM runtime_events WHERE run_id = ? AND event_order = ?",
                (run_id, event_order),
            ).fetchone()
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        if row is None:
            return None
        cols = _event_row_columns()
        return _event_to_dict(tuple(row), cols)

    def read_events(self, run_id: str) -> list[dict]:
        """Read all events for a run, ordered by event_order, gap-free verified."""
        self._conn.execute("BEGIN")
        try:
            self._ensure_compatibility()
            rows = self._conn.execute(
                "SELECT * FROM runtime_events WHERE run_id = ? ORDER BY event_order",
                (run_id,),
            ).fetchall()
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        if not rows:
            return []

        cols = _event_row_columns()
        events = [_event_to_dict(tuple(row), cols) for row in rows]

        # Verify gap-free ordering
        for i, event in enumerate(events, 1):
            if isinstance(event["event_order"], int):
                pass  # ok
            if type(event["event_order"]) is not int or event["event_order"] != i:
                raise RuntimeJournalError({
                    "code": "gap_detected",
                    "expected_order": i,
                    "actual_order": event.get("event_order"),
                    "run_id": run_id,
                })

        return events

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------


def _build_stored_event(
    request: dict,
    event_id: str,
    event_order: int,
    occurred_at: str,
    schema_version: str,
    prior_state: dict,
    next_state: dict,
) -> dict:
    """Construct the complete stored RuntimeEvent dict (without content_digest yet)."""
    event = dict(request)
    event["event_id"] = event_id
    event["event_order"] = event_order
    event["occurred_at"] = occurred_at
    event["schema_version"] = schema_version
    event["prior_state"] = prior_state
    event["next_state"] = next_state
    return event


# ---------------------------------------------------------------------------
# Read-only evidence snapshot (public API)
# ---------------------------------------------------------------------------

def _reconstruct_events(rows, columns: list[str]) -> list[dict]:
    """Reconstruct RuntimeEvent objects preserving exact causation shape.

    Each reconstructed event must preserve the exact presence/absence of
    causation_id and causation_chain from the original AppendRequest:
    exactly one of the two fields must remain (the one whose database
    column was non-NULL); the other is completely omitted from the dict.
    Database-only fields (event_id, event_order, occurred_at,
    schema_version, prior_state, next_state, content_digest) remain
    present.  NULL JSON columns are not materialised as Python None.
    """
    events: list[dict] = []
    json_fields = {
        "payload", "trigger_artifact", "expected_stream_head",
        "prior_state", "next_state",
    }
    for row in rows:
        d = dict(zip(columns, tuple(row)))
        # Deserialise JSON fields
        for field in json_fields:
            val = d.get(field)
            if val is not None and isinstance(val, str):
                d[field] = json.loads(val)

        # causation_chain: omit if NULL in DB
        cchain = d.get("causation_chain")
        if cchain is not None:
            if isinstance(cchain, str):
                d["causation_chain"] = json.loads(cchain)
        else:
            d.pop("causation_chain", None)

        # causation_id: omit if NULL in DB
        cid = d.get("causation_id")
        if cid is None:
            d.pop("causation_id", None)

        # -- Validate exactly-one causation form and types --
        has_cid = "causation_id" in d
        has_cchain = "causation_chain" in d
        if has_cid and has_cchain:
            # Both present -- invalid
            raise RuntimeJournalError({
                "code": "evidence_snapshot_incomplete",
            })
        if not has_cid and not has_cchain:
            # Neither present -- invalid
            raise RuntimeJournalError({
                "code": "evidence_snapshot_incomplete",
            })
        if has_cid:
            cid_val = d.get("causation_id")
            if not isinstance(cid_val, str) or cid_val == "":
                raise RuntimeJournalError({
                    "code": "evidence_snapshot_incomplete",
                })
        if has_cchain:
            cchain_val = d.get("causation_chain")
            if not isinstance(cchain_val, list):
                raise RuntimeJournalError({
                    "code": "evidence_snapshot_incomplete",
                })
            for item in cchain_val:
                if not isinstance(item, str):
                    raise RuntimeJournalError({
                        "code": "evidence_snapshot_incomplete",
                    })

        events.append(d)
    return events


def read_run_evidence_snapshot(db_path: str, run_id: str) -> dict:
    """Capture one run's complete events, persisted receipts, and stream head
    from a single read-only SQLite snapshot.

    Opens the database with SQLite URI ``mode=ro``, begins one read
    transaction, and reads events, idempotency receipts, and stream head
    within that snapshot.  Returns a deep-independent result dict that
    callers may mutate freely.

    Returns:
      {
        "run_id": str,
        "events": list[dict],
        "receipts": list[dict],
        "source_stream_head": {"event_order": int, "content_digest": str},
      }

    Raises ``RuntimeJournalError`` with a structured code on error.
    """
    # -- Validate arguments --
    if not isinstance(db_path, str) or not db_path:
        raise RuntimeJournalError({"code": "run_not_found"})
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeJournalError({"code": "run_not_found"})

    # -- Path isolation: reject .workflow paths (case-insensitive) --
    resolved = pathlib.Path(db_path).resolve()
    for part in resolved.parts:
        if part.lower() == ".workflow":
            raise RuntimeJournalError({
                "code": "workflow_path_rejected",
                "path": str(resolved),
            })

    # -- Verify path is a regular file (reject dirs, sockets, devices, etc.) --
    if not resolved.is_file():
        raise RuntimeJournalError({"code": "run_not_found"})

    # -- Open with mode=ro; no WAL, no CREATE, no sidecar --
    uri = _readonly_database_uri(resolved)
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        # -- Begin single read transaction --
        conn.execute("BEGIN")
        try:
            # -- Workflow schema gate --
            existing: set[str] = set()
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ):
                existing.add(row["name"])
            found = existing & _WORKFLOW_TABLES
            if found:
                raise RuntimeJournalError({
                    "code": "workflow_schema_rejected",
                    "tables": sorted(found),
                })

            # -- All three runtime reference tables must exist --
            required_tables = {"runtime_events", "idempotency", "stream_heads"}
            missing_tables = required_tables - existing
            if missing_tables:
                raise RuntimeJournalError({"code": "run_not_found"})

            # -- Schema version: reject non-0.9.0 or mixed --
            versions = {
                row["schema_version"]
                for row in conn.execute(
                    "SELECT DISTINCT schema_version FROM runtime_events"
                )
            }
            if versions and versions != {_SCHEMA_VERSION}:
                raise RuntimeJournalError({"code": "unsupported_schema_version"})

            # -- Read all events for the run --
            event_rows = conn.execute(
                "SELECT * FROM runtime_events WHERE run_id = ? ORDER BY event_order",
                (run_id,),
            ).fetchall()

            if not event_rows:
                raise RuntimeJournalError({"code": "run_not_found"})

            cols = _event_row_columns()
            events = _reconstruct_events(event_rows, cols)

            # -- Read persisted receipts (idempotency) and verify --
            receipts: list[dict] = []
            for event in events:
                ceid = event["client_event_id"]
                idem_row = conn.execute(
                    "SELECT stored_receipt FROM idempotency WHERE client_event_id = ?",
                    (ceid,),
                ).fetchone()

                if idem_row is None:
                    raise RuntimeJournalError({
                        "code": "evidence_snapshot_incomplete",
                    })

                receipt_bytes = idem_row["stored_receipt"]
                if isinstance(receipt_bytes, bytes):
                    receipt = json.loads(receipt_bytes.decode("utf-8"))
                else:
                    receipt = json.loads(receipt_bytes)

                # Verify receipt identity matches its event
                if receipt.get("event_id") != event.get("event_id"):
                    raise RuntimeJournalError({
                        "code": "evidence_snapshot_incomplete",
                    })
                if receipt.get("event_order") != event.get("event_order"):
                    raise RuntimeJournalError({
                        "code": "evidence_snapshot_incomplete",
                    })
                if receipt.get("stored_content_digest") != event.get("content_digest"):
                    raise RuntimeJournalError({
                        "code": "evidence_snapshot_incomplete",
                    })

                receipts.append(receipt)

            # -- Read stream head --
            head_row = conn.execute(
                "SELECT event_order, content_digest FROM stream_heads WHERE run_id = ?",
                (run_id,),
            ).fetchone()

            if head_row is None:
                raise RuntimeJournalError({
                    "code": "evidence_snapshot_incomplete",
                })

            source_stream_head = {
                "event_order": int(head_row["event_order"]),
                "content_digest": head_row["content_digest"],
            }

            # -- Verify final receipt / head match --
            last_event = events[-1]
            last_receipt = receipts[-1]
            if last_event["event_order"] != source_stream_head["event_order"]:
                raise RuntimeJournalError({
                    "code": "evidence_snapshot_incomplete",
                })
            if last_event["content_digest"] != source_stream_head["content_digest"]:
                raise RuntimeJournalError({
                    "code": "evidence_snapshot_incomplete",
                })
            if last_receipt.get("event_order") != source_stream_head["event_order"]:
                raise RuntimeJournalError({
                    "code": "evidence_snapshot_incomplete",
                })
            new_head = last_receipt.get("new_stream_head", {})
            if new_head.get("content_digest") != source_stream_head["content_digest"]:
                raise RuntimeJournalError({
                    "code": "evidence_snapshot_incomplete",
                })

            conn.execute("COMMIT")
        except RuntimeJournalError:
            conn.execute("ROLLBACK")
            raise
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise RuntimeJournalError({"code": "evidence_snapshot_incomplete"})
    finally:
        conn.close()

    # Return a deep-independent copy (JSON round-trip detaches from DB rows)
    return {
        "run_id": run_id,
        "events": events,
        "receipts": receipts,
        "source_stream_head": source_stream_head,
    }
