"""Read-only in-process runtime evidence exporter.

Assembles a complete v1.1.0 export envelope from the accepted atomic
journal snapshot and in-memory projection APIs.  Performs no
persistence, no transport, no SQLite access, no signing, and no
file-system writes.

Exposes exactly one public function:
  export_run(db_path, run_id, *, export_id, exported_at) -> dict

The exporter calls read_run_evidence_snapshot exactly once and
run_projection_from_events exactly once.  All envelope components
are deep-copied from those two sources so callers may mutate the
returned dict freely.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import re

from scripts.runtime_state_core import canonical_serialize
from scripts.runtime_state_journal import read_run_evidence_snapshot, RuntimeJournalError
from scripts.runtime_state_projection import run_projection_from_events, ProjectionError

_EXPORT_ID_RE = re.compile(r"^export-[0-9a-f-]+$")
_VALID_VISIBILITIES = {"public", "project", "restricted"}
_VALID_CONTRIBUTOR_KINDS = {
    "trigger_provenance",
    "project_policy",
    "governing_contract",
    "contained_artifact",
}


class RuntimeEvidenceExportError(Exception):
    """Structured exporter error with a code and optional detail dict."""

    def __init__(self, code: str, detail: dict | None = None):
        self.code = code
        self.detail = detail or {}
        super().__init__(code)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_run(db_path: str, run_id: str, *, export_id: str, exported_at: str) -> dict:
    """Assemble a complete v1.1.0 export envelope for *run_id*.

    The caller must supply an explicit non-empty *export_id* matching
    ``^export-[0-9a-f-]+$`` and an explicit timezone-bearing ISO-8601
    *exported_at*.  Neither is generated inside the exporter.

    Returns the full envelope dict.  Raises
    :class:`RuntimeEvidenceExportError` on exporter-owned failures and
    preserves dependency error identity (``RuntimeJournalError``,
    ``ProjectionError``) without converting partial results into a
    partial envelope.
    """
    _validate_params(db_path, run_id, export_id, exported_at)

    # -- Step 2: Obtain journal snapshot (exactly one call) --
    snapshot = read_run_evidence_snapshot(db_path, run_id)
    events = copy.deepcopy(snapshot["events"])
    receipts = copy.deepcopy(snapshot["receipts"])
    source_stream_head = copy.deepcopy(snapshot["source_stream_head"])

    # -- Step 3: Obtain in-memory projection (exactly one call) --
    projection = run_projection_from_events(run_id, events)

    # -- Step 4: Build visibility_basis --
    basis = _build_visibility_basis(projection)

    # -- Step 5: Resolve visibility --
    resolved_visibility = _resolve_visibility(basis)

    # Verify it equals projection.resolved_run_visibility
    proj_vis = projection.get("resolved_run_visibility", "")
    if resolved_visibility != proj_vis:
        raise RuntimeEvidenceExportError(
            "visibility_mismatch",
            {
                "resolved_from_basis": resolved_visibility,
                "projection_resolved_run_visibility": proj_vis,
            },
        )

    # -- Step 6: Build provenance --
    provenance = _build_provenance(projection)

    # -- Step 7: Assemble envelope --
    envelope = {
        "export_version": "1.1.0",
        "export_id": export_id,
        "exported_at": exported_at,
        "run_id": run_id,
        "source_stream_head": source_stream_head,
        "events": copy.deepcopy(events),
        "receipts": copy.deepcopy(receipts),
        "projection": copy.deepcopy(projection),
        "visibility": resolved_visibility,
        "visibility_basis": _deepcopy_basis(basis),
        "provenance": copy.deepcopy(provenance),
    }

    # -- Step 8: Compute export_content_digest --
    preimage = {k: v for k, v in envelope.items() if k != "export_content_digest"}
    digest_bytes = hashlib.sha256(canonical_serialize(preimage)).hexdigest()
    envelope["export_content_digest"] = f"sha256:{digest_bytes}"

    # -- Step 9: Return envelope --
    return envelope


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_params(db_path: str, run_id: str, export_id: str, exported_at: str) -> None:
    """Validate all exporter parameters before touching the journal."""

    if not isinstance(db_path, str) or not db_path:
        raise RuntimeEvidenceExportError("invalid_db_path")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeEvidenceExportError("invalid_run_id")
    if not isinstance(export_id, str) or not export_id:
        raise RuntimeEvidenceExportError("invalid_export_id")
    if _EXPORT_ID_RE.fullmatch(export_id) is None:
        raise RuntimeEvidenceExportError(
            "invalid_export_id",
            {"export_id": export_id, "pattern": "^export-[0-9a-f-]+$"},
        )
    if not isinstance(exported_at, str) or not exported_at:
        raise RuntimeEvidenceExportError("invalid_exported_at")
    # Require timezone-bearing ISO-8601
    try:
        parsed = datetime.datetime.fromisoformat(
            exported_at.replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        raise RuntimeEvidenceExportError(
            "invalid_exported_at",
            {"exported_at": exported_at, "reason": "cannot parse as ISO 8601"},
        )
    if parsed.tzinfo is None:
        raise RuntimeEvidenceExportError(
            "invalid_exported_at",
            {"exported_at": exported_at, "reason": "missing timezone"},
        )


def _build_visibility_basis(projection: dict) -> list[dict]:
    """Build the ordered visibility_basis array from projection data.

    Order is strictly:
      1. trigger_visibility  → contributor_kind "trigger_provenance"
      2. policy_contributors → contributor_kind "project_policy" (recorder order)
      3. contract_contributors → contributor_kind "governing_contract" (recorder order)
      4. runtime_artifacts with explicit visibility → contributor_kind "contained_artifact"
         (recorder order)

    Rejects: empty basis, duplicate identities, invalid asserted_visibility,
    and missing/invalid contributor facts.
    """
    basis: list[dict] = []
    vctx = projection.get("visibility_context", {})

    # 1. trigger_visibility
    tv = vctx.get("trigger_visibility")
    if tv is not None and isinstance(tv, dict):
        entry = _build_visibility_entry(tv, "trigger_provenance")
        if entry is not None:
            basis.append(entry)

    # 2. policy_contributors
    for pc in vctx.get("policy_contributors", []) or []:
        entry = _build_visibility_entry(pc, "project_policy")
        if entry is not None:
            basis.append(entry)

    # 3. contract_contributors
    for cc in vctx.get("contract_contributors", []) or []:
        entry = _build_visibility_entry(cc, "governing_contract")
        if entry is not None:
            basis.append(entry)

    # 4. runtime_artifacts with explicit visibility
    for art in projection.get("runtime_artifacts", []) or []:
        if not isinstance(art, dict):
            continue
        # Only include artifacts that have an explicit visibility classification
        vis = art.get("visibility")
        if vis is None or vis not in _VALID_VISIBILITIES:
            continue
        art_ref = art.get("artifact_ref")
        if not isinstance(art_ref, dict):
            continue
        if not art_ref.get("artifact_id") or not art_ref.get("artifact_kind"):
            continue
        # Build the basis entry
        vis_resolution = art.get("visibility_resolution")
        vis_ctx_art = {
            "contributor_ref": copy.deepcopy(art_ref),
            "asserted_visibility": vis,
            "authority": "",
            "contributor_kind": "contained_artifact",
        }
        if isinstance(vis_resolution, dict):
            vis_ctx_art["authority"] = vis_resolution.get(
                "resolution_rule", ""
            )
            audit = vis_resolution.get("resolution_audit", {})
            if isinstance(audit, dict) and audit.get("applied_rule"):
                vis_ctx_art["authority"] = (
                    f"{vis_ctx_art['authority']} applied_rule={audit.get('applied_rule')}"
                ).strip()
        if not vis_ctx_art["authority"]:
            vis_ctx_art["authority"] = (
                f"Artifact {art_ref.get('artifact_id', 'unknown')} at {vis}"
            )
        entry = _build_visibility_entry(vis_ctx_art, "contained_artifact")
        if entry is not None:
            basis.append(entry)

    # Validate: non-empty
    if not basis:
        raise RuntimeEvidenceExportError("empty_visibility_basis")

    # Validate: no duplicate identities, valid asserted_visibility
    _validate_basis(basis)

    return basis


def _build_visibility_entry(
    source: dict, contributor_kind: str
) -> dict | None:
    """Build a single VisibilityBasisEntry from a visibility contributor source.

    Returns None for structural failures (missing contributor_ref, etc.).
    """
    if contributor_kind not in _VALID_CONTRIBUTOR_KINDS:
        return None

    contributor_ref = source.get("contributor_ref")
    if not isinstance(contributor_ref, dict):
        return None
    if not contributor_ref.get("artifact_id") or not contributor_ref.get(
        "artifact_kind"
    ):
        return None

    asserted_visibility = source.get("asserted_visibility")
    if asserted_visibility not in _VALID_VISIBILITIES:
        return None

    # Build rationale from authority / resolution data
    authority = source.get("authority", "")
    rationale = authority if isinstance(authority, str) and authority.strip() else ""
    if not rationale:
        resolution_audit = source.get("resolution_audit")
        if isinstance(resolution_audit, dict):
            applied = resolution_audit.get("applied_rule", "")
            if applied:
                rationale = f"Resolved via {applied}"
    if not rationale:
        rationale = (
            f"Declared {asserted_visibility} by {contributor_kind} "
            f"{contributor_ref.get('artifact_id', 'unknown')}"
        )

    return {
        "contributor_kind": contributor_kind,
        "contributor": {
            k: v
            for k, v in contributor_ref.items()
            if k
            in {
                "artifact_id",
                "artifact_kind",
                "artifact_version",
                "locator",
                "digest",
            }
        },
        "asserted_visibility": asserted_visibility,
        "rationale": rationale,
    }


def _validate_basis(basis: list[dict]) -> None:
    """Validate the visibility_basis array for integrity.

    Rejects:
    - empty basis
    - duplicate (contributor_kind, contributor.artifact_id, contributor.artifact_kind)
    - invalid asserted_visibility
    - invalid/missing contributor facts
    """
    seen: set[tuple] = set()

    for entry in basis:
        ck = entry.get("contributor_kind")
        if ck not in _VALID_CONTRIBUTOR_KINDS:
            raise RuntimeEvidenceExportError(
                "invalid_visibility_basis",
                {"reason": f"invalid contributor_kind: {ck}"},
            )

        av = entry.get("asserted_visibility")
        if av not in _VALID_VISIBILITIES:
            raise RuntimeEvidenceExportError(
                "invalid_visibility_basis",
                {"reason": f"invalid asserted_visibility: {av}"},
            )

        contributor = entry.get("contributor", {})
        aid = contributor.get("artifact_id")
        ak = contributor.get("artifact_kind")
        if not aid or not ak:
            raise RuntimeEvidenceExportError(
                "invalid_visibility_basis",
                {"reason": "missing contributor artifact_id or artifact_kind"},
            )

        identity = (ck, aid, ak)
        if identity in seen:
            raise RuntimeEvidenceExportError(
                "duplicate_visibility_basis_identity",
                {
                    "contributor_kind": ck,
                    "artifact_id": aid,
                    "artifact_kind": ak,
                },
            )
        seen.add(identity)

        rationale = entry.get("rationale", "")
        if not isinstance(rationale, str) or not rationale.strip():
            raise RuntimeEvidenceExportError(
                "invalid_visibility_basis",
                {"reason": "empty rationale"},
            )

    if not basis:
        raise RuntimeEvidenceExportError("empty_visibility_basis")


def _resolve_visibility(basis: list[dict]) -> str:
    """Resolve visibility using restricted > project > public partial order."""
    order = {"public": 1, "project": 2, "restricted": 3}
    resolved = "public"
    for entry in basis:
        av = entry.get("asserted_visibility", "public")
        if order.get(av, 0) > order.get(resolved, 0):
            resolved = av
    return resolved


def _build_provenance(projection: dict) -> dict:
    """Build the provenance section from projection data.

    Raises RuntimeEvidenceExportError on missing/invalid provenance.
    """
    run_prov = projection.get("run_provenance")
    if not isinstance(run_prov, dict):
        raise RuntimeEvidenceExportError("provenance_missing")

    origin = run_prov.get("origin_artifact")
    if not isinstance(origin, dict):
        raise RuntimeEvidenceExportError("provenance_missing")
    if not origin.get("artifact_id") or not origin.get("artifact_kind"):
        raise RuntimeEvidenceExportError("provenance_missing")
    valid_kinds = {"ticket", "pipeline_config", "script", "request_artifact"}
    if origin.get("artifact_kind") not in valid_kinds:
        raise RuntimeEvidenceExportError(
            "provenance_missing",
            {"invalid_artifact_kind": origin.get("artifact_kind")},
        )

    gcs = run_prov.get("governing_contracts")
    if not isinstance(gcs, list) or not gcs:
        raise RuntimeEvidenceExportError("provenance_missing")
    for gc in gcs:
        if not isinstance(gc, dict) or not gc.get("artifact_id") or not gc.get("artifact_kind"):
            raise RuntimeEvidenceExportError("provenance_missing")

    return {
        "origin_artifact": copy.deepcopy(origin),
        "governing_contracts": copy.deepcopy(gcs),
    }


def _deepcopy_basis(basis: list[dict]) -> list[dict]:
    """Deep copy the visibility_basis list."""
    return copy.deepcopy(basis)
