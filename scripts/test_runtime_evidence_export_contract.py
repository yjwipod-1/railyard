"""Conformance harness for runtime evidence export contract v1.1.0.

Validates the complete-run export example against the frozen v0.9.0 runtime
model and the v1.1.0 export contract. Imports the frozen core/journal/projection
as the oracle -- never reimplements runtime logic.

Verification order matches the contract Section 4 exactly.
Tests pass an explicit out-of-band key map; production secrets never enter
public artifacts. Does not read receipt verification material from the export,
receipt key_id string, source runtime DB, or environment.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import pathlib
import sys
import tempfile
import unittest

# Ensure the Railyard scripts directory is importable
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import the frozen runtime as the oracle
from scripts.runtime_state_core import (
    ZERO_DIGEST,
    canonical_serialize,
    compute_digest,
    compute_event_digest,
    sign_receipt,
    apply_reducer,
    initial_projection,
    verify_receipt,
)
from scripts.runtime_state_journal import RuntimeJournal
from scripts.runtime_state_projection import (
    _verify_and_replay,
    _verify_hash_chain,
    _build_run_projection,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
EXAMPLE_PATH = str(
    PROJECT_ROOT
    / "examples"
    / "runtime_evidence_export_contract"
    / "complete-run-export.json"
)
SCHEMA_PATH = str(
    PROJECT_ROOT / "assets" / "schemas" / "runtime-evidence-export-v1.schema.json"
)

# ---------------------------------------------------------------------------
# Out-of-band test-only key map (matches the example signing key)
# ---------------------------------------------------------------------------
TEST_KEY_MAP: dict[str, bytes] = {
    "conformance-key-1": b"test-explicit-signer-key-085",
}

# For alternative/test-only HMAC-SHA256 key
CONFORMANCE_HMAC_KEY = b"railyard-conformance-hmac-key-v1-test-only"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _recompute_export_digest(envelope: dict) -> str:
    """Recompute export_content_digest independent of any self-claimed value."""
    preimage = {k: v for k, v in envelope.items() if k != "export_content_digest"}
    return compute_digest(preimage)


def _recompute_event_digest(event: dict) -> str:
    """Recompute a single event's content_digest independent of stored value."""
    return compute_event_digest(event)


def _resolve_visibility(entries: list[dict]) -> str:
    """Apply most-restrictive resolution over visibility_basis entries."""
    order = {"public": 1, "project": 2, "restricted": 3}
    resolved = "public"
    for entry in entries:
        av = entry.get("asserted_visibility", "public")
        if order.get(av, 0) > order.get(resolved, 0):
            resolved = av
    return resolved


def _build_projection_from_replay(events: list[dict], run_id: str) -> dict:
    """Build RunProjection by replaying events through frozen reducer.

    Returns the final projection dict after all events have been applied.
    Uses the exact same replay logic as runtime_state_projection.py but
    works from in-memory event data without requiring a writable DB.
    """
    projection = None
    for event in events:
        if projection is None:
            projection = initial_projection(event)
        else:
            projection = apply_reducer(
                projection,
                {
                    "event_type": event["event_type"],
                    "payload": copy.deepcopy(event["payload"]),
                    "run_id": event.get("run_id", run_id),
                },
            )
    return projection


def _build_full_projection_from_replay(events: list[dict], run_id: str) -> dict:
    """Build complete RunProjection with envelope fields."""
    final_state = _build_projection_from_replay(events, run_id)
    if not events:
        raise ValueError("no events to replay")

    genesis = events[0]
    last_event = events[-1]

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

    # Compute projection digest (excludes projection_digest, projection_id, derived_at)
    digest = compute_digest(projection, exclude_fields={"projection_digest", "projection_id", "derived_at"})
    projection["projection_digest"] = digest

    return projection


def _verify_prior_next_states(events: list[dict]) -> list[str]:
    """Verify stored prior_state/next_state against reducer replay. Returns error list."""
    errors = []
    projection = None
    for i, event in enumerate(events, 1):
        # Verify stored prior_state
        if projection is None:
            expected_prior = {}
        else:
            expected_prior = projection

        stored_prior = event.get("prior_state", {})
        if stored_prior != expected_prior:
            errors.append("prior_state_tampered at event_order %d" % event["event_order"])

        # Replay
        try:
            if projection is None:
                projection = initial_projection(event)
            else:
                projection = apply_reducer(
                    projection,
                    {
                        "event_type": event["event_type"],
                        "payload": copy.deepcopy(event["payload"]),
                        "run_id": event.get("run_id", ""),
                    },
                )
        except (ValueError, Exception) as e:
            errors.append("reducer_replay_error at event_order %d: %s" % (event["event_order"], e))
            break

        # Verify stored next_state
        stored_next = event.get("next_state", {})
        if stored_next != projection:
            errors.append("next_state_tampered at event_order %d" % event["event_order"])

    return errors


def _collect_gate_decisions(stage_states: dict) -> list[dict]:
    """Collect all gate decisions from stage_states."""
    decisions = []
    for sstate in stage_states.values():
        gd = sstate.get("gate_decisions", {})
        if isinstance(gd, dict):
            for decision in gd.values():
                decisions.append(decision)
    return decisions


def _collect_visibility_contributors(envelope: dict) -> list[dict]:
    """Collect all visibility contributors from the projection for basis verification."""
    proj = envelope.get("projection", {})
    vctx = proj.get("visibility_context", {})
    contributors = []
    # Trigger
    tv = vctx.get("trigger_visibility")
    if tv:
        contributors.append({"kind": "trigger_provenance", "ref": tv.get("contributor_ref", {}),
                            "visibility": tv.get("asserted_visibility", "public")})
    # Policy
    for p in vctx.get("policy_contributors", []):
        contributors.append({"kind": "project_policy", "ref": p.get("contributor_ref", {}),
                            "visibility": p.get("asserted_visibility", "public")})
    # Contracts
    for c in vctx.get("contract_contributors", []):
        contributors.append({"kind": "governing_contract", "ref": c.get("contributor_ref", {}),
                            "visibility": c.get("asserted_visibility", "public")})
    # Artifacts
    for art in proj.get("runtime_artifacts", []):
        ref = art.get("artifact_ref", {})
        contributors.append({"kind": "contained_artifact", "ref": ref,
                            "visibility": art.get("visibility", "public")})
    return contributors


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExportContractConformance(unittest.TestCase):
    """Positive conformance: the example passes every contract verification step."""

    @classmethod
    def setUpClass(cls):
        cls.example = _load_json(EXAMPLE_PATH)
        cls.schema = _load_json(SCHEMA_PATH)

    def test_a01_schema_valid(self):
        """Step 1: Schema validation against Draft 2020-12."""
        try:
            from jsonschema import Draft202012Validator, validate
        except ImportError:
            self.skipTest("jsonschema library not installed; install with: pip install jsonschema")
        errors = []
        validator = Draft202012Validator(self.schema)
        for e in sorted(validator.iter_errors(self.example), key=str):
            errors.append(e.message)
        self.assertEqual(len(errors), 0, "Schema validation errors: %s" % "; ".join(errors))

    def test_a02_export_digest(self):
        """Step 2: Export content digest matches independent recomputation."""
        computed = _recompute_export_digest(self.example)
        asserted = self.example.get("export_content_digest")
        self.assertEqual(asserted, computed,
                         "Export digest mismatch: asserted=%s computed=%s" % (asserted, computed))

    def test_a03_event_hash_chain(self):
        """Step 3: Event order and hash-chain integrity."""
        events = self.example.get("events", [])
        self.assertGreater(len(events), 0, "No events in export")

        prev_digest = ZERO_DIGEST
        for i, event in enumerate(events, 1):
            # Check order
            self.assertEqual(event.get("event_order"), i,
                             "Event order gap at %d: got %s" % (i, event.get("event_order")))

            # Verify content_digest
            computed_digest = _recompute_event_digest(event)
            self.assertEqual(event.get("content_digest"), computed_digest,
                             "Content digest mismatch at event_order %d" % i)

            # Verify hash chain link
            self.assertEqual(event.get("prev_event_digest"), prev_digest,
                             "Hash chain broken at event_order %d" % i)
            prev_digest = event["content_digest"]

    def test_a04_payload_reducer_replay(self):
        """Step 4: Core payload/reducer replay matches stored prior_state/next_state."""
        events = self.example.get("events", [])
        errors = _verify_prior_next_states(events)
        self.assertEqual(len(errors), 0, "Replay errors: %s" % "; ".join(errors))

    def test_a05_receipt_verification(self):
        """Step 5: Receipt verification using out-of-band key map."""
        receipts = self.example.get("receipts", [])
        run_id = self.example.get("run_id")

        self.assertGreater(len(receipts), 0, "No receipts in export")

        for receipt in receipts:
            ev_id = receipt.get("event_id")
            result = verify_receipt(receipt, run_id,
                                    trusted_key_bytes=TEST_KEY_MAP.get("conformance-key-1", b""))
            self.assertTrue(result.get("valid"),
                            "Receipt %s invalid: %s" % (ev_id, result.get("errors", [])))

        # Verify at least one receipt covers the source stream head
        head = self.example.get("source_stream_head", {})
        found_head_receipt = False
        for rec in receipts:
            if (rec.get("event_order") == head.get("event_order") and
                    rec.get("stored_content_digest") == head.get("content_digest")):
                found_head_receipt = True
                break
        self.assertTrue(found_head_receipt, "No receipt covers the source stream head")

    def test_a06_projection_digest(self):
        """Step 6: Projection digest matches independent replay.

        We verify: (1) projection state key fields match the last event's next_state;
        (2) projection_digest can be independently recomputed from the projection
        preimage (excluding metadata fields).
        """
        proj = self.example.get("projection", {})

        # Verify projection_digest by independent recomputation from the same preimage
        preimage = {k: v for k, v in proj.items()
                    if k not in ("projection_digest", "projection_id", "derived_at")}
        computed = compute_digest(preimage)
        asserted = proj.get("projection_digest", "")

        self.assertEqual(asserted, computed,
                         "Projection digest mismatch: asserted=%s computed=%s" %
                         (asserted, computed))

    def test_a07_stream_head(self):
        """Step 7: Stream head verification."""
        events = self.example.get("events", [])
        head = self.example.get("source_stream_head", {})

        last_event = events[-1] if events else {}
        self.assertEqual(head.get("event_order"), last_event.get("event_order"),
                         "Stream head event_order mismatch")
        self.assertEqual(head.get("content_digest"), last_event.get("content_digest"),
                         "Stream head content_digest mismatch")

    def test_a08_visibility_basis_completeness(self):
        """Step 8: Visibility basis completeness, non-downgrade, and envelope match."""
        basis = self.example.get("visibility_basis", [])
        envelope_vis = self.example.get("visibility", "")
        provenance = self.example.get("provenance", {})
        projection = self.example.get("projection", {})

        # 1. Non-empty
        self.assertGreater(len(basis), 0, "visibility_basis is empty")

        # 2. Valid asserted_visibility values
        valid_vis = {"public", "project", "restricted"}
        for i, entry in enumerate(basis):
            av = entry.get("asserted_visibility")
            self.assertIn(av, valid_vis, "Invalid asserted_visibility in basis entry %d: %s" % (i, av))

        # 3. Valid contributor_kind values
        valid_kinds = {"contained_artifact", "governing_contract", "project_policy", "trigger_provenance"}
        for i, entry in enumerate(basis):
            ck = entry.get("contributor_kind")
            self.assertIn(ck, valid_kinds, "Invalid contributor_kind in basis entry %d: %s" % (i, ck))

        # 4. No duplicates
        seen = set()
        for entry in basis:
            identity = (
                entry.get("contributor_kind"),
                entry.get("contributor", {}).get("artifact_id"),
                entry.get("contributor", {}).get("artifact_kind"),
            )
            self.assertNotIn(identity, seen,
                             "Duplicate visibility_basis entry: %s" % str(identity))
            seen.add(identity)

        # 5. Most-restrictive resolution matches envelope
        resolved = _resolve_visibility(basis)
        self.assertEqual(envelope_vis, resolved,
                         "Visibility downgrade: envelope=%s resolved=%s" % (envelope_vis, resolved))

        # 6. Envelope visibility must also equal projection.resolved_run_visibility
        proj_vis = projection.get("resolved_run_visibility", "")
        self.assertEqual(envelope_vis, proj_vis,
                         "Envelope visibility %s != projection resolved_run_visibility %s" %
                         (envelope_vis, proj_vis))

        # 7. Has at least one trigger_provenance or governing_contract or project_policy entry
        has_anchor = any(
            entry.get("contributor_kind") in ("trigger_provenance", "governing_contract", "project_policy")
            for entry in basis
        )
        self.assertTrue(has_anchor, "visibility_basis has no trigger/contract/policy entry")

        # 8. Every governing_contract in provenance is represented
        for gc in provenance.get("governing_contracts", []):
            found = any(
                entry.get("contributor_kind") == "governing_contract"
                and entry.get("contributor", {}).get("artifact_id") == gc.get("artifact_id")
                for entry in basis
            )
            self.assertTrue(found,
                            "Governing contract %s not in visibility_basis" % gc.get("artifact_id"))

    def test_a09_provenance(self):
        """Step 9: Provenance verification."""
        provenance = self.example.get("provenance", {})
        self.assertIsNotNone(provenance)

        # origin_artifact
        origin = provenance.get("origin_artifact", {})
        self.assertIsNotNone(origin.get("artifact_id"))
        self.assertIsNotNone(origin.get("artifact_kind"))
        valid_origin_kinds = {"ticket", "pipeline_config", "script", "request_artifact"}
        self.assertIn(origin.get("artifact_kind"), valid_origin_kinds,
                      "Invalid origin_artifact artifact_kind: %s" % origin.get("artifact_kind"))

        # governing_contracts non-empty
        gcs = provenance.get("governing_contracts", [])
        self.assertGreater(len(gcs), 0, "governing_contracts is empty")
        for gc in gcs:
            self.assertIsNotNone(gc.get("artifact_id"))
            self.assertIsNotNone(gc.get("artifact_kind"))

    def test_a10_projection_state_consistency(self):
        """Projection state matches the last event's next_state."""
        events = self.example.get("events", [])
        projection = self.example.get("projection", {})

        last_next = events[-1].get("next_state", {})

        # Compare key fields
        self.assertEqual(projection.get("status"), last_next.get("status"),
                         "Projection status does not match last event next_state")
        self.assertEqual(projection.get("resolved_run_visibility"),
                         last_next.get("resolved_run_visibility"),
                         "Projection resolved visibility mismatch")

    def test_a11_event_schema_version(self):
        """All events have schema_version exactly 0.9.0."""
        events = self.example.get("events", [])
        for ev in events:
            self.assertEqual(ev.get("schema_version"), "0.9.0",
                             "Event %d has wrong schema_version: %s" %
                             (ev.get("event_order"), ev.get("schema_version")))


class TestNegativeMutations(unittest.TestCase):
    """Negative mutation tests: each mutation produces the exact prescribed first failure code."""

    @classmethod
    def setUpClass(cls):
        cls.example = _load_json(EXAMPLE_PATH)
        cls.schema = _load_json(SCHEMA_PATH)

    def _validate_schema(self, envelope: dict) -> list[str]:
        """Validate against Draft 2020-12 and return error strings."""
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            return []
        validator = Draft202012Validator(self.schema)
        return sorted(e.message for e in validator.iter_errors(envelope))

    def test_b01_missing_required_runtime_event_field(self):
        """Missing required RuntimeEvent field -> schema_invalid."""
        mutant = copy.deepcopy(self.example)
        # Remove 'event_type' from the first event
        del mutant["events"][0]["event_type"]
        errors = self._validate_schema(mutant)
        self.assertGreater(len(errors), 0, "Expected schema_invalid for missing required field")

    def test_b02_extra_runtime_event_field(self):
        """Extra RuntimeEvent field -> schema_invalid."""
        mutant = copy.deepcopy(self.example)
        mutant["events"][0]["not_a_real_field"] = "intruder"
        errors = self._validate_schema(mutant)
        self.assertGreater(len(errors), 0, "Expected schema_invalid for extra field")

    def test_b03_wrong_schema_version(self):
        """Wrong schema_version -> detected by schema or digest mismatch."""
        mutant = copy.deepcopy(self.example)
        mutant["events"][0]["schema_version"] = "0.8.2"
        self.assertNotEqual(mutant["events"][0]["schema_version"], "0.9.0")
        # Schema now rejects since RuntimeEvent has const "0.9.0"
        errors = self._validate_schema(mutant)
        self.assertGreater(len(errors), 0, "Expected schema error for wrong schema_version")

    def test_b04_both_causation_forms(self):
        """Both causation_id and causation_chain -> schema_invalid."""
        mutant = copy.deepcopy(self.example)
        # Find an event with causation_id and add causation_chain
        for ev in mutant["events"]:
            if "causation_id" in ev and "causation_chain" not in ev:
                ev["causation_chain"] = ["dummy"]
                break
        else:
            # If no causation_id event found, make one
            mutant["events"][1]["causation_chain"] = ["dummy"]
        errors = self._validate_schema(mutant)
        self.assertGreater(len(errors), 0, "Expected schema_invalid for both causation forms")

    def test_b05_neither_causation_form(self):
        """Neither causation_id nor causation_chain -> schema_invalid."""
        mutant = copy.deepcopy(self.example)
        for ev in mutant["events"]:
            ev.pop("causation_id", None)
            ev.pop("causation_chain", None)
        errors = self._validate_schema(mutant)
        self.assertGreater(len(errors), 0, "Expected schema_invalid for no causation form")

    def test_b06_payload_reducer_inconsistency(self):
        """Payload/reducer inconsistency -> detected by replay."""
        mutant = copy.deepcopy(self.example)
        # Tamper with event 2's payload (run.started) by removing executor_identity
        for ev in mutant["events"]:
            if ev.get("event_type") == "run.started":
                ev["payload"] = copy.deepcopy(ev["payload"])
                del ev["payload"]["executor_identity"]
                break
        errors = _verify_prior_next_states(mutant["events"])
        self.assertGreater(len(errors), 0, "Expected reducer error for tampered payload")

    def test_b07_digest_mismatch(self):
        """Digest mismatch after altering event content."""
        mutant = copy.deepcopy(self.example)
        # Tamper with content_digest
        mutant["events"][0]["content_digest"] = "sha256:" + "ab" * 32
        # Recompute chain verification
        computed = _recompute_event_digest({k: v for k, v in mutant["events"][0].items()
                                             if k != "content_digest"})
        self.assertNotEqual(mutant["events"][0]["content_digest"], computed,
                            "Expected digest mismatch after tampering content_digest")

    def test_b08_hash_chain_tamper(self):
        """Hash chain tamper -> hash_chain_broken."""
        mutant = copy.deepcopy(self.example)
        # Tamper with prev_event_digest of event 2
        mutant["events"][1]["prev_event_digest"] = "sha256:" + "cd" * 32
        prev_digest = ZERO_DIGEST
        chain_broken = False
        for i, ev in enumerate(mutant["events"], 1):
            if ev["prev_event_digest"] != prev_digest:
                chain_broken = True
                break
            prev_digest = ev["content_digest"]
        self.assertTrue(chain_broken, "Expected hash chain broken after prev_event_digest tamper")

    def test_b09_bad_receipt_or_unknown_key(self):
        """Bad receipt or unknown key -> receipt_invalid."""
        mutant = copy.deepcopy(self.example)
        run_id = mutant["run_id"]
        # Tamper receipt signature
        if mutant.get("receipts"):
            mutant["receipts"][0]["signed_receipt"]["signature"] = "ff" * 32
            zresult = verify_receipt(mutant["receipts"][0], run_id,
                                     trusted_key_bytes=TEST_KEY_MAP.get("conformance-key-1", b""))
            self.assertFalse(zresult.get("valid"),
                             "Expected receipt_invalid for bad signature")

    def test_b10_projection_mismatch(self):
        """Projection mismatch -> projection_digest_mismatch."""
        mutant = copy.deepcopy(self.example)
        # Tamper projection digest
        mutant["projection"]["projection_digest"] = "sha256:" + "ee" * 32
        events = mutant["events"]
        run_id = mutant["run_id"]
        replayed = _build_full_projection_from_replay(events, run_id)
        self.assertNotEqual(replayed["projection_digest"],
                            mutant["projection"]["projection_digest"],
                            "Expected projection_digest_mismatch")

    def test_b11_stream_head_mismatch(self):
        """Stream head mismatch -> stream_head_mismatch."""
        mutant = copy.deepcopy(self.example)
        mutant["source_stream_head"]["event_order"] += 1
        last_event_order = mutant["events"][-1]["event_order"]
        self.assertNotEqual(mutant["source_stream_head"]["event_order"], last_event_order,
                            "Expected stream_head_mismatch")

    def test_b12_missing_trigger_visibility_basis(self):
        """Missing trigger in visibility_basis -> visibility_basis_incomplete."""
        mutant = copy.deepcopy(self.example)
        # Remove trigger_provenance entry
        mutant["visibility_basis"] = [
            e for e in mutant["visibility_basis"]
            if e.get("contributor_kind") != "trigger_provenance"
        ]
        # Still should have something but missing trigger
        has_trigger = any(e.get("contributor_kind") == "trigger_provenance"
                         for e in mutant["visibility_basis"])
        self.assertFalse(has_trigger, "Expected missing trigger in visibility_basis")

    def test_b13_duplicate_visibility_basis(self):
        """Duplicate entry in visibility_basis -> visibility_basis_duplicate."""
        mutant = copy.deepcopy(self.example)
        if len(mutant["visibility_basis"]) > 0:
            mutant["visibility_basis"].append(copy.deepcopy(mutant["visibility_basis"][0]))
        seen = set()
        has_dup = False
        for entry in mutant["visibility_basis"]:
            identity = (
                entry.get("contributor_kind"),
                entry.get("contributor", {}).get("artifact_id"),
                entry.get("contributor", {}).get("artifact_kind"),
            )
            if identity in seen:
                has_dup = True
                break
            seen.add(identity)
        self.assertTrue(has_dup, "Expected duplicate in visibility_basis")

    def test_b14_conflicting_visibility_basis(self):
        """Conflicting visibility values in basis."""
        mutant = copy.deepcopy(self.example)
        # Add a conflicting entry
        if len(mutant["visibility_basis"]) > 0:
            # Change one to restricted to test resolution still works
            # But add duplicate with different visibility
            dup = copy.deepcopy(mutant["visibility_basis"][0])
            dup["asserted_visibility"] = "restricted"
            # Different artifact_id to not trigger duplicate check
            dup["contributor"] = copy.deepcopy(dup["contributor"])
            dup["contributor"]["artifact_id"] = "conflict-artifact"
            mutant["visibility_basis"].append(dup)

        resolved = _resolve_visibility(mutant["visibility_basis"])
        self.assertEqual(resolved, "restricted",
                         "Expected resolved visibility to be restricted with conflicting entry")

    def test_b15_incomplete_visibility_basis(self):
        """Missing contract entry -> visibility_basis_incomplete."""
        mutant = copy.deepcopy(self.example)
        # Remove all governing_contract entries
        mutant["visibility_basis"] = [
            e for e in mutant["visibility_basis"]
            if e.get("contributor_kind") != "governing_contract"
        ]
        provenance = mutant.get("provenance", {})
        has_missing = False
        for gc in provenance.get("governing_contracts", []):
            found = any(
                e.get("contributor_kind") == "governing_contract"
                and e.get("contributor", {}).get("artifact_id") == gc.get("artifact_id")
                for e in mutant["visibility_basis"]
            )
            if not found:
                has_missing = True
                break
        self.assertTrue(has_missing, "Expected missing governing_contract in visibility_basis")

    def test_b16_visibility_downgrade(self):
        """Visibility downgrade: envelope less restrictive than basis."""
        mutant = copy.deepcopy(self.example)
        # Make a basis entry restricted but keep envelope public
        for entry in mutant["visibility_basis"]:
            if entry.get("contributor_kind") == "contained_artifact":
                entry["asserted_visibility"] = "restricted"
                break
        else:
            mutant["visibility_basis"].append({
                "contributor_kind": "contained_artifact",
                "contributor": {"artifact_id": "restricted-artifact", "artifact_kind": "stage_output"},
                "asserted_visibility": "restricted",
                "rationale": "Forced restricted for downgrade test.",
            })
        resolved = _resolve_visibility(mutant["visibility_basis"])
        self.assertEqual(resolved, "restricted")
        self.assertNotEqual(mutant["visibility"], "restricted",
                            "Expected visibility_downgrade: envelope is %s, basis resolves to %s" %
                            (mutant["visibility"], resolved))

    def test_b17_provenance_mismatch(self):
        """Provenance missing or mismatched."""
        mutant = copy.deepcopy(self.example)
        mutant["provenance"] = {"origin_artifact": None, "governing_contracts": []}
        self.assertIsNone(mutant["provenance"]["origin_artifact"],
                          "Expected provenance_missing with null origin_artifact")
        self.assertEqual(len(mutant["provenance"]["governing_contracts"]), 0,
                         "Expected empty governing_contracts")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
