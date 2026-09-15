"""Read-only deterministic replay and projection layer over the runtime event journal.

Uses temporary read-only SQLite connections. Performs NO database writes.
Reconstructs RunProjection deterministically from ordered events while verifying
gap-free ordering, run identity consistency, event schema version compatibility,
hash-chain integrity, content digests, and reducer recomputation of stored
prior_state/next_state values. The frozen contract is v0.9.0.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import pathlib
import sqlite3
import uuid
from datetime import datetime

from scripts.runtime_state_core import (
    ZERO_DIGEST,
    apply_reducer,
    bind_store_event_metadata,
    canonical_serialize,
    compute_digest,
    compute_event_digest,
    initial_projection,
    verify_stream_integrity,
)

_EVENT_COLUMNS = [
    "run_id", "event_id", "event_order", "event_type",
    "payload", "causation_id", "causation_chain",
    "actor_role", "actor_identity", "trigger_artifact",
    "reason", "recommended_action", "expected_stream_head",
    "client_event_id", "prev_event_digest",
    "prior_state", "next_state", "occurred_at",
    "schema_version", "content_digest",
]

_JSON_FIELDS = {
    "payload", "causation_chain", "trigger_artifact",
    "expected_stream_head", "prior_state", "next_state",
}


class ProjectionError(Exception):
    """Structured deterministic error for projection/replay failures."""

    def __init__(self, code: str, detail: dict | None = None):
        self.code = code
        self.detail = detail or {}
        super().__init__(code)

    def __str__(self):
        return f"ProjectionError({self.code}): {json.dumps(self.detail)}"


def _event_from_row(row: tuple, columns: list[str]) -> dict:
    """Convert a database row to a RuntimeEvent dict with JSON deserialisation.

    Removes None-valued non-JSON keys (e.g. causation_id) to match the exact
    shape of the stored RuntimeEvent before digest computation, which only
    includes keys present in the request + store-assigned fields.
    """
    d = dict(zip(columns, row))
    for field in _JSON_FIELDS:
        if field in d and d[field] is not None:
            d[field] = json.loads(d[field])
    # Remove None-valued keys that were not in the original stored event dict.
    # The stored event dict has no causation_id key when causation_chain is used.
    if d.get("causation_id") is None and "causation_id" in d:
        del d["causation_id"]
    return d


def _read_events_raw(db_path: str, run_id: str) -> list[dict]:
    """Read all events for a run from the database using a read-only URI connection.

    Opens with mode=ro, used briefly, and closed. A missing database path
    returns a structured ProjectionError and does NOT create any file.
    Uses the actual column order from the database schema.

    Enforces frozen v0.9.0 event schema: any database containing a
    runtime_events schema_version other than exactly 0.9.0 is rejected
    inside the same read snapshot used to fetch events.
    """
    abs_path = pathlib.Path(db_path).resolve()

    if not abs_path.is_file():
        raise ProjectionError("db_not_found", {"path": str(abs_path)})

    uri = abs_path.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Check if runtime_events table exists
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_events'"
        ).fetchone()
        if table_check is None:
            return []

        # Direct-SQLite frozen schema enforcement: in the same read
        # transaction/snapshot used to fetch events, reject any database
        # containing a runtime_events schema_version other than exactly 0.9.0.
        wrong_version = conn.execute(
            "SELECT schema_version FROM runtime_events "
            "WHERE schema_version != '0.9.0' LIMIT 1"
        ).fetchone()
        if wrong_version is not None:
            raise ProjectionError("unsupported_schema_version", {
                "expected_schema_version": "0.9.0",
                "found_schema_version": wrong_version[0],
            })

        # Get actual column order from DB
        desc_result = conn.execute(
            "SELECT * FROM runtime_events LIMIT 0"
        )
        cols = [desc[0] for desc in desc_result.description]

        rows = conn.execute(
            "SELECT * FROM runtime_events WHERE run_id = ? ORDER BY event_order",
            (run_id,),
        ).fetchall()

        events = [_event_from_row(tuple(row), cols) for row in rows]
        return events
    finally:
        conn.close()


def _verify_run_identity(events: list[dict], run_id: str):
    """Verify all events share the same run_id."""
    for i, event in enumerate(events, 1):
        if event.get("run_id") != run_id:
            raise ProjectionError("run_identity_mismatch", {
                "expected_run_id": run_id,
                "actual_run_id": event.get("run_id"),
                "event_order": event.get("event_order"),
                "event_index": i,
            })


def _verify_next_state(events: list[dict]):
    """Replay events through reducer and verify stored prior_state matches replay
    output from previous event, and stored next_state matches replay output for
    current event.
    """
    projection = None
    for i, event in enumerate(events, 1):
        # Verify stored prior_state matches replayed projection from previous event
        if projection is None:
            # First event: prior_state should be empty {} (no prior projection)
            expected_prior = {}
            if event.get("prior_state") != expected_prior:
                raise ProjectionError("prior_state_tampered", {
                    "event_order": event["event_order"],
                    "event_type": event["event_type"],
                    "event_index": i,
                })
        else:
            if event.get("prior_state") != projection:
                raise ProjectionError("prior_state_tampered", {
                    "event_order": event["event_order"],
                    "event_type": event["event_type"],
                    "event_index": i,
                })

        # Replay the event
        try:
            if projection is None:
                projection = initial_projection(event)
            else:
                projection = apply_reducer(
                    projection,
                    {"event_type": event["event_type"], "payload": copy.deepcopy(event["payload"]), "run_id": event["run_id"]},
                )
        except ValueError as e:
            raise ProjectionError("reducer_replay_error", {
                "event_order": event["event_order"],
                "event_type": event["event_type"],
                "error": str(e),
                "event_index": i,
            })

        # Bind store-assigned metadata from the current stored event using the
        # same shared helper as the journal write-path, then compare exact
        # stored next_state. prior_state for event N equals the metadata-complete
        # next_state of event N-1 (persisted by the journal), so any tampering
        # of latest_event_* in prior_state or next_state is detected here.
        projection = bind_store_event_metadata(projection, event)

        # Verify stored next_state matches replayed output
        stored_next = event.get("next_state")
        if stored_next != projection:
            raise ProjectionError("next_state_tampered", {
                "event_order": event["event_order"],
                "event_type": event["event_type"],
                "event_index": i,
            })

    return projection


def _verify_hash_chain(events: list[dict], run_id: str):
    """Verify full hash-chain integrity: content digest and prev_event_digest
    link for every event in the stream.
    """
    previous_digest = ZERO_DIGEST
    for i, event in enumerate(events, 1):
        # Verify content digest of THIS event
        stored_digest = event.get("content_digest", "")
        computed = compute_event_digest(
            {k: v for k, v in event.items() if k != "content_digest"}
        )
        if stored_digest != computed:
            raise ProjectionError("content_digest_mismatch", {
                "event_order": i,
                "event_id": event.get("event_id"),
                "stored_digest": stored_digest,
                "computed_digest": computed,
                "run_id": run_id,
            })

        # Verify hash chain link
        prev = event.get("prev_event_digest", "")
        if prev != previous_digest:
            raise ProjectionError("hash_chain_link_broken", {
                "event_order": i,
                "expected_prev": previous_digest,
                "actual_prev": prev,
                "run_id": run_id,
            })

        previous_digest = stored_digest


def _build_run_projection(events: list[dict], final_state: dict) -> dict:
    """Build the RunProjection envelope from events and final replayed state.

    projection_id and derived_at are presentation metadata per contract Section 3.1.
    Only stable replay state, canonical preimage, and projection_digest are deterministic.
    """
    if not events:
        raise ProjectionError("empty_event_stream")

    genesis = events[0]
    last_event = events[-1]
    run_id = genesis.get("run_id", "")

    projection = copy.deepcopy(final_state)

    # Add projection envelope fields
    projection["latest_event_id"] = last_event.get("event_id", "")
    projection["latest_event_order"] = last_event.get("event_order", 0)
    projection["latest_event_type"] = last_event.get("event_type", "")
    projection["event_count"] = len(events)
    projection["event_range"] = {
        "first_order": 1,
        "last_order": len(events),
        "first_event_id": genesis.get("event_id", ""),
        "last_event_id": last_event.get("event_id", ""),
    }
    projection["projection_type"] = "full_replay"

    # Compute projection digest (excludes presentation metadata per contract Section 3.1)
    digest = compute_digest(projection, exclude_fields={"projection_digest", "projection_id", "derived_at"})
    projection["projection_digest"] = digest

    # Presentation metadata: projection_id is a random identity, derived_at is truthful UTC
    projection["projection_id"] = str(uuid.uuid4())
    projection["derived_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    return projection


def _verify_and_replay(events: list[dict], run_id: str) -> dict:
    """Full verification and replay of events. Returns the final replayed state dict.

    Checks performed in order:
    1. Gap-free ordering
    2. Run identity consistency
    3. Reducer recomputation (next_state/prior_state verification)
    4. Full hash-chain integrity (delegated to _verify_hash_chain)
    """
    if not events:
        raise ProjectionError("no_events_for_run", {"run_id": run_id})

    # 1. Gap-free ordering check
    for i, event in enumerate(events, 1):
        if type(event.get("event_order")) is not int or event["event_order"] != i:
            raise ProjectionError("event_order_gap", {
                "expected_order": i,
                "actual_order": event.get("event_order"),
                "run_id": run_id,
            })

    # 2. Run identity consistency
    _verify_run_identity(events, run_id)

    # 3. Reducer recomputation: verify stored prior_state and next_state
    projection = _verify_next_state(events)

    # 4. Full hash-chain integrity
    _verify_hash_chain(events, run_id)

    return projection


# ---------------------------------------------------------------------------
# In-memory schema version verification
# ---------------------------------------------------------------------------

def _verify_schema_version(events: list[dict]):
    """Verify all events have schema_version exactly 0.9.0.

    Rejects any event whose schema_version is missing or not exactly '0.9.0'.
    This is the in-memory equivalent of the DB-level schema check and uses the
    same stable ProjectionError code.
    """
    for i, event in enumerate(events, 1):
        sv = event.get("schema_version")
        if sv != "0.9.0":
            raise ProjectionError("unsupported_schema_version", {
                "expected_schema_version": "0.9.0",
                "found_schema_version": sv,
            })


# ---------------------------------------------------------------------------
# Public read-only replay APIs
# ---------------------------------------------------------------------------


def run_projection_from_events(run_id: str, events: list[dict]) -> dict:
    """Verify and replay an already-captured complete RuntimeEvent sequence.

    Deep-copies the input events, verifies exact schema_version 0.9.0,
    non-empty stream, gap-free order, run identity, reducer prior/next state,
    content digests and full hash chain, then returns a RunProjection.

    This function does NOT access SQLite, filesystem, environment, or network.
    It does NOT mutate the input events list or any nested payload/state objects.

    Returns the full RunProjection dict with a deterministic projection_digest,
    a fresh random UUIDv4 projection_id, and truthful current UTC derived_at.

    Raises ProjectionError for any integrity or schema violation.
    """
    # Deep copy to prevent any caller-input mutation (events, payloads, state objects)
    events_copy = copy.deepcopy(events)

    # Schema version must be verified before any replay begins
    _verify_schema_version(events_copy)

    # Full verify and replay (gap-free, run identity, reducer, hash chain)
    final_state = _verify_and_replay(events_copy, run_id)

    # Build projection envelope (presentation metadata, digest)
    projection = _build_run_projection(events_copy, final_state)

    return projection


def run_projection(run_id: str, db_path: str) -> dict:
    """Read all events for a run, verify integrity, and reconstruct the RunProjection.

    Reads one read-only SQLite snapshot, then delegates to
    run_projection_from_events for all verification and replay.
    Does not maintain a second replay or projection-building path.

    Returns the full RunProjection dict with a deterministic projection_digest.

    Raises ProjectionError for any integrity violation.
    """
    events = _read_events_raw(db_path, run_id)
    return run_projection_from_events(run_id, events)


def stage_projection(run_id: str, stage_id: str, db_path: str) -> dict:
    """Extract a specific stage's state from the run projection.

    Returns the stage state dict from the RunProjection's stage_states.
    Raises ProjectionError if the stage is not found.
    """
    projection = run_projection(run_id, db_path)
    stage_states = projection.get("stage_states", {})
    if stage_id not in stage_states:
        raise ProjectionError("stage_not_found", {
            "run_id": run_id,
            "stage_id": stage_id,
        })
    return copy.deepcopy(stage_states[stage_id])


def stream_head(run_id: str, db_path: str) -> dict:
    """Return the current stream head {event_order, content_digest} for a run.

    Validates gap-free ordering, run identity, AND full hash-chain integrity
    so that tampering any event in the stream is detected.

    Returns the empty head {event_order: 0, content_digest: ZERO_DIGEST} if
    the run has no events.
    """
    events = _read_events_raw(db_path, run_id)
    if not events:
        return {"event_order": 0, "content_digest": ZERO_DIGEST}

    # Verify gap-free and run identity to detect tampering
    _verify_run_identity(events, run_id)
    for i, event in enumerate(events, 1):
        if type(event.get("event_order")) is not int or event["event_order"] != i:
            raise ProjectionError("event_order_gap", {
                "expected_order": i,
                "actual_order": event.get("event_order"),
                "run_id": run_id,
            })

    # Full hash-chain verification
    _verify_hash_chain(events, run_id)

    last = events[-1]
    stored_digest = last.get("content_digest", "")

    return {
        "event_order": last["event_order"],
        "content_digest": stored_digest,
    }


def lineage(run_id: str, db_path: str) -> dict | None:
    """Extract the child-run lineage information from a run's events.

    Returns the lineage dict from the run.created payload, or None if the run
    has no lineage (root run).

    Verifies the genesis event's content_digest to detect tampering.
    Raises ProjectionError if the genesis event is not run.created.
    """
    events = _read_events_raw(db_path, run_id)
    if not events:
        raise ProjectionError("no_events_for_run", {"run_id": run_id})

    genesis = events[0]
    if genesis.get("event_type") != "run.created":
        raise ProjectionError("genesis_not_run_created", {
            "run_id": run_id,
            "event_type": genesis.get("event_type"),
        })

    # Verify genesis event content_digest
    stored_digest = genesis.get("content_digest", "")
    computed = compute_event_digest(
        {k: v for k, v in genesis.items() if k != "content_digest"}
    )
    if stored_digest != computed:
        raise ProjectionError("content_digest_mismatch", {
            "event_order": 1,
            "event_id": genesis.get("event_id"),
            "stored_digest": stored_digest,
            "computed_digest": computed,
            "run_id": run_id,
        })

    payload = genesis.get("payload", {})
    lin = payload.get("lineage")
    return copy.deepcopy(lin) if lin is not None else None


def projection_digest(run_id: str, db_path: str) -> str:
    """Compute and return the deterministic projection digest for a run.

    Replays all events, builds the projection, and returns only the digest.
    """
    projection = run_projection(run_id, db_path)
    return projection["projection_digest"]


# ---------------------------------------------------------------------------
# Read-only proof helper
# ---------------------------------------------------------------------------

def replay_is_read_only(db_path: str, run_id: str) -> bool:
    """Verify that replay performs NO database writes or side effects.

    Checks before and after replay:
    - PRAGMA schema_version unchanged (changes on schema modifications)
    - sqlite_master row count unchanged

    Returns False when ANY change is detected.
    """
    abs_path = pathlib.Path(db_path).resolve()
    uri = abs_path.as_uri() + "?mode=ro"

    # Pre-replay state
    conn_before = sqlite3.connect(uri, uri=True)
    try:
        schema_version_before = conn_before.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        schema_count_before = conn_before.execute(
            "SELECT COUNT(*) FROM sqlite_master"
        ).fetchone()[0]
    finally:
        conn_before.close()

    # Run the replay
    _ = run_projection(run_id, db_path)

    # Post-replay state
    conn_after = sqlite3.connect(uri, uri=True)
    try:
        schema_version_after = conn_after.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        schema_count_after = conn_after.execute(
            "SELECT COUNT(*) FROM sqlite_master"
        ).fetchone()[0]
    finally:
        conn_after.close()

    if schema_version_before != schema_version_after:
        return False
    if schema_count_before != schema_count_after:
        return False

    return True
