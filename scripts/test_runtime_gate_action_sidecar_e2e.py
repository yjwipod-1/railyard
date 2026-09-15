"""E2E conformance: Gate Decision -> Runtime Action Policy -> RuntimeStateSidecar.

Standard-library unittest suite. It calls the accepted public evaluators
(evaluate_gate, evaluate_runtime_action) and uses an explicit caller-owned
mapping to append only authorized outcomes through RuntimeStateSidecar.

Strict, independent verification:
  * Every append carries an explicit causation_chain (GateDecision.decision_id
    for gate events, RuntimeActionDecision.decision_id for action and
    child-genesis events). No default or generated causation id is used.
  * All eight authorized action mappings are appended through the real
    sidecar with complete Runtime State lineage for child genesis.
  * Independent event / HMAC / projection / export / chain oracles recompute
    every digest with standard-library code only. They do not import any
    production serialization, digest, or receipt helper.
  * Deterministic ids, timestamps, signer key, and paths. No uuid, random,
    clock, network, or subprocess dependency appears in this file.
  * Zero-write probes catch the exact public error type and assert its stable
    code; they never swallow exceptions.
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

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from runtime_gate_decision import evaluate_gate
from runtime_action_policy import evaluate_runtime_action as eval_action
from runtime_state_sidecar import RuntimeStateSidecar, RuntimeStateSidecarError
from runtime_state_journal import RuntimeJournalError

# ---------------------------------------------------------------------------
# Local deterministic constants (no uuid / random / time)
# ---------------------------------------------------------------------------

ZERO_DIGEST = "sha256:" + "0" * 64
SIGNER_KEY = b"test-explicit-signer-key-085"
BASE_TIME = "2026-07-27T00:00:00Z"

GATE_ID = "check-build"
STAGE_ID = "build"
SIGNAL_REF = {"artifact_id": "signal-report", "artifact_kind": "report", "artifact_version": "1"}
CONTRACT_REF = {"artifact_id": "conformance-contract", "artifact_kind": "contract", "artifact_version": "1"}
EVIDENCE_REF = {"artifact_id": "conformance-evidence", "artifact_kind": "evidence", "artifact_version": "1"}


# ---------------------------------------------------------------------------
# Independent RFC 8785 JCS canonical serialization (stdlib only)
# ---------------------------------------------------------------------------

def _oracle_canonical(obj) -> bytes:
    """Canonicalize a value per RFC 8785 JCS (UTF-16-BE key sort).

    Mirrors the production RFC 8785 serializer byte-for-byte using only the
    standard library. Non-ASCII strings are emitted verbatim (ensure_ascii
    disabled), matching the journal's serializer.
    """

    def _validate(item):
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, str):
            if any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise ValueError("canonical: lone surrogate rejected")
            return
        if isinstance(item, int) and not isinstance(item, bool):
            if not (-(2 ** 53) + 1 <= item <= (2 ** 53) - 1):
                raise ValueError("canonical: integer out of I-JSON range")
            return
        if isinstance(item, list):
            for child in item:
                _validate(child)
            return
        if isinstance(item, dict):
            for key in item:
                if not isinstance(key, str):
                    raise ValueError("canonical: non-string object key")
            for child in item.values():
                _validate(child)
            return
        raise ValueError("canonical: unsupported type")

    def _serialize(item) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False)
        if isinstance(item, int) and not isinstance(item, bool):
            return str(item)
        if isinstance(item, list):
            return "[" + ",".join(_serialize(child) for child in item) + "]"
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + _serialize(item[key])
            for key in sorted(item, key=lambda k: k.encode("utf-16-be"))
        ) + "}"

    _validate(obj)
    return _serialize(obj).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(obj: dict, exclude: set | None = None) -> str:
    preimage = {k: v for k, v in obj.items() if k not in (exclude or set())}
    return "sha256:" + _sha256_hex(_oracle_canonical(preimage))


# ---------------------------------------------------------------------------
# Independent crypto oracles (stdlib only, no production helpers)
# ---------------------------------------------------------------------------

def _event_content_digest(event: dict) -> str:
    """Recompute a stored event content_digest independently.

    The production digest hashes the AppendRequest dict (which never carried a
    null causation key). The stored event returned by the reader materialises
    a null causation column, so we drop any null causation field before
    canonicalizing to reproduce the exact production preimage.
    """
    preimage = {k: v for k, v in event.items() if k != "content_digest"}
    if preimage.get("causation_id") is None:
        preimage.pop("causation_id", None)
    if preimage.get("causation_chain") is None:
        preimage.pop("causation_chain", None)
    return _digest(preimage)


def _verify_receipt_signature(receipt: dict, key: bytes = SIGNER_KEY) -> bool:
    """Independently verify a signed receipt HMAC-SHA256 signature."""
    signed = receipt.get("signed_receipt")
    if not isinstance(signed, dict):
        return False
    payload = signed.get("signed_payload")
    if not isinstance(payload, dict):
        return False
    expected = hmac.new(key, _oracle_canonical(payload), hashlib.sha256).hexdigest()
    signature = signed.get("signature")
    if not isinstance(signature, str):
        return False
    return hmac.compare_digest(expected, signature)


def _projection_digest(projection: dict) -> str:
    return _digest(projection, exclude={"projection_digest", "projection_id", "derived_at"})


def _export_digest(envelope: dict) -> str:
    return _digest(envelope, exclude={"export_content_digest"})


def _verify_chain(snapshot: dict) -> None:
    """Verify gap-free order, hash chain, recomputed digests, and receipts."""
    events = snapshot["events"]
    receipts = snapshot["receipts"]
    for i, ev in enumerate(events, 1):
        if ev["event_order"] != i:
            raise AssertionError(f"gap_detected at order {i}")
        if i == 1:
            if ev["prev_event_digest"] != ZERO_DIGEST:
                raise AssertionError("genesis prev_digest not ZERO_DIGEST")
        else:
            if ev["prev_event_digest"] != events[i - 2]["content_digest"]:
                raise AssertionError(f"chain broken at order {i}")
        if ev["content_digest"] != _event_content_digest(ev):
            raise AssertionError(f"content_digest mismatch at order {i}")
    for r in receipts:
        if not _verify_receipt_signature(r):
            raise AssertionError("receipt signature mismatch")


# ---------------------------------------------------------------------------
# Deterministic request builders
# ---------------------------------------------------------------------------

def _gate_declaration(gate_type="custom", required=True, failure_behavior="halt_stage"):
    dec = {
        "gate_id": GATE_ID,
        "gate_type": gate_type,
        "required": required,
        "failure_behavior": failure_behavior,
    }
    if gate_type == "validator":
        dec["contract_ref"] = copy.deepcopy(CONTRACT_REF)
    if not required:
        dec["allow_gate_override"] = False
    return dec


def _gate_request(outcome, gate_type="custom", classification="complete",
                  failure_behavior="halt_stage", decision_id="gate-dec-default"):
    signal_key = "overall_verdict" if gate_type == "validator" else "outcome"
    ref_name = {
        "validator": "report_ref",
        "artifact_shape": "artifact_ref",
        "diff_review": "diff_ref",
        "custom": "custom_source_ref",
    }[gate_type]
    signal = {ref_name: copy.deepcopy(SIGNAL_REF), signal_key: outcome}
    if outcome != "pass":
        code_map = {
            "fail": "validator_fail_deterministic",
            "blocked": "evidence_incomplete",
            "inconclusive": "evidence_absent_inconclusive",
            "human_review_required": "evidence_absent_human",
        }
        signal["failure_code"] = code_map[outcome]
    envelope = {
        "envelope_id": f"env-{outcome}",
        "gate_id": GATE_ID,
        "primary_evidence": [copy.deepcopy(EVIDENCE_REF)],
        "evidence_classification": classification,
        "collected_at": BASE_TIME,
        "collected_by": "collector",
    }
    if classification != "complete":
        envelope["missing_evidence_description"] = ["missing-item"]
    if gate_type == "validator":
        envelope["validation_report"] = copy.deepcopy(SIGNAL_REF)
    return {
        "request_kind": "initial",
        "decision_id": decision_id,
        "evaluated_at": BASE_TIME,
        "evaluated_by": "runner-1",
        "gate_declaration": _gate_declaration(gate_type, failure_behavior=failure_behavior),
        "evidence_envelope": envelope,
        "evaluation_signal": signal,
        "run_context": {"run_id": "_placeholder_run", "stage_id": STAGE_ID},
        "execution_mode": "full",
    }


def _pass_gate_decision():
    return evaluate_gate(_gate_request("pass", decision_id="gate-dec-pass-001"))


def _fail_gate_decision_stop_stage():
    return evaluate_gate(_gate_request("fail", failure_behavior="halt_stage",
                                       decision_id="gate-dec-fail-stage-001"))


def _fail_gate_decision_stop_run():
    return evaluate_gate(_gate_request("fail", failure_behavior="halt_run",
                                       decision_id="gate-dec-fail-run-001"))


def _blocked_gate_decision():
    return evaluate_gate(_gate_request("blocked", classification="partial_recoverable",
                                       decision_id="gate-dec-blocked-001"))


def _human_review_decision_proceed_with_warning():
    data = _gate_request("fail", failure_behavior="warn", decision_id="gate-dec-warn-001")
    data["gate_declaration"]["required"] = False
    data["gate_declaration"]["allow_gate_override"] = False
    return evaluate_gate(data)


def _make_gate_binding(gate_decision):
    snap = {
        "decision_id": gate_decision["decision_id"],
        "gate_id": gate_decision["gate_id"],
        "outcome": gate_decision["outcome"],
        "execution_mode": gate_decision["execution_mode"],
        "evidence": gate_decision["evidence"],
        "recommendation": gate_decision["recommendation"],
        "evaluated_at": gate_decision["evaluated_at"],
        "evaluated_by": gate_decision["evaluated_by"],
        "run_context": gate_decision["run_context"],
    }
    if "failure_code" in gate_decision:
        snap["failure_code"] = gate_decision["failure_code"]
        snap["failure_description"] = gate_decision["failure_description"]
    digest = _compute_canonical_gate_digest(snap)
    return {
        "source_gate_decision_ref": {
            "artifact_id": gate_decision["decision_id"],
            "artifact_kind": "gate-decision",
            "digest": digest,
        },
        "gate_decision_snapshot": snap,
        "canonical_digest": digest,
    }


def _compute_canonical_gate_digest(snapshot):
    text = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + _sha256_hex(text.encode("utf-8"))


def _policy_declaration():
    return {
        "contract_id": "runtime-action-policy-contract",
        "contract_version": "2.0.0",
        "policy_id": "P-CONF-1",
        "evaluated_under": "runtime-action-policy-evaluator-v2",
    }


def _action_auth(role="architect"):
    return {
        "authorized_by": role,
        "authorized_at": BASE_TIME,
        "authorization_id": "az-architect-001",
        "reason": "Conformance test authorization.",
    }


def _stop_stage_request(run_id):
    gd = _fail_gate_decision_stop_stage()
    binding = _make_gate_binding(gd)
    return copy.deepcopy({
        "policy_declaration": _policy_declaration(),
        "decision_id": "act-dec-stop-stage-001",
        "evaluated_at": BASE_TIME,
        "evaluated_by": "architect",
        "run_id": run_id,
        "action_kind": "stop_stage",
        "boundary_facts": {
            "parent_run_id": run_id,
            "parent_run_status": "active",
            "relevant_stage_id": STAGE_ID,
            "relevant_stage_status": "active",
        },
        "gate_snapshot_binding": binding,
    })


def _stop_run_request(run_id):
    gd = _fail_gate_decision_stop_run()
    binding = _make_gate_binding(gd)
    return copy.deepcopy({
        "policy_declaration": _policy_declaration(),
        "decision_id": "act-dec-stop-run-001",
        "evaluated_at": BASE_TIME,
        "evaluated_by": "architect",
        "run_id": run_id,
        "action_kind": "stop_run",
        "boundary_facts": {
            "parent_run_id": run_id,
            "parent_run_status": "active",
        },
        "gate_snapshot_binding": binding,
    })


def _retry_request(run_id, child_run_id):
    return copy.deepcopy({
        "policy_declaration": _policy_declaration(),
        "decision_id": "act-dec-retry-001",
        "evaluated_at": BASE_TIME,
        "evaluated_by": "architect",
        "run_id": run_id,
        "action_kind": "retry",
        "proposed_child_run_id": child_run_id,
        "retry_strategy": "full",
        "failure_category": "command_failed",
        "authorization": _action_auth("architect"),
        "boundary_facts": {
            "parent_run_id": run_id,
            "parent_run_status": "failed",
            "current_retry_count": 1,
            "max_retries": 3,
            "same_kind_failure_count": 0,
            "attempt_history_facts": {
                "attempt_count": 1,
                "last_failure_category": "command_failed",
                "last_failure_transient": True,
                "last_failure_deterministic": True,
            },
        },
    })


def _resume_request(run_id, child_run_id):
    return copy.deepcopy({
        "policy_declaration": _policy_declaration(),
        "decision_id": "act-dec-resume-001",
        "evaluated_at": BASE_TIME,
        "evaluated_by": "architect",
        "run_id": run_id,
        "action_kind": "resume",
        "proposed_child_run_id": child_run_id,
        "authorization": _action_auth("architect"),
        "boundary_facts": {
            "parent_run_id": run_id,
            "parent_run_status": "interrupted",
            "checkpoint_available": True,
            "interruption_cause": "session_lost",
            "checkpoint_event_order": 4,
        },
        "checkpoint": {
            "checkpoint_ref": {"artifact_id": "cp-1", "artifact_kind": "checkpoint"},
            "checkpoint_event_order": 4,
            "checkpoint_stage_id": STAGE_ID,
            "recovery_action": "replay_from_checkpoint",
            "artifacts_produced_before_checkpoint": [
                {"artifact_id": "ev-1", "artifact_kind": "evidence"},
            ],
        },
    })


def _more_evidence_request(run_id):
    gd = _blocked_gate_decision()
    binding = _make_gate_binding(gd)
    return copy.deepcopy({
        "policy_declaration": _policy_declaration(),
        "decision_id": "act-dec-more-evidence-001",
        "evaluated_at": BASE_TIME,
        "evaluated_by": "architect",
        "run_id": run_id,
        "action_kind": "more_evidence",
        "boundary_facts": {
            "parent_run_id": run_id,
            "parent_run_status": "active",
            "evidence_gap_reason": "missing_evidence",
        },
        "gate_snapshot_binding": binding,
        "proposed_child_lineage": {"parent_run_id": run_id, "lineage_kind": "more_evidence"},
        "evidence_requests": [
            {
                "request_id": "er-1",
                "artifact_kind": "evidence",
                "description": "Additional conformance evidence required.",
                "required": True,
            }
        ],
    })


def _redesign_request(run_id):
    return copy.deepcopy({
        "policy_declaration": _policy_declaration(),
        "decision_id": "act-dec-redesign-001",
        "evaluated_at": BASE_TIME,
        "evaluated_by": "architect",
        "run_id": run_id,
        "action_kind": "redesign",
        "boundary_facts": {
            "parent_run_id": run_id,
            "parent_run_status": "failed",
        },
        "proposed_child_lineage": {"parent_run_id": run_id, "lineage_kind": "redesign"},
        "revised_contract_ref": {"artifact_id": "rc-1", "artifact_kind": "contract"},
        "reason_code": "contract_incomplete",
        "authorization": _action_auth("architect"),
        "history_preservation_facts": {
            "original_history_preserved": True,
            "original_evidence_preserved": True,
        },
    })


def _human_intervention_request(run_id, intervention_mode="policy_exhaustion"):
    if intervention_mode == "gate_recommendation":
        gd = _human_review_decision_proceed_with_warning()
        binding = _make_gate_binding(gd)
        return copy.deepcopy({
            "policy_declaration": _policy_declaration(),
            "decision_id": "act-dec-human-gate-001",
            "evaluated_at": BASE_TIME,
            "evaluated_by": "architect",
            "run_id": run_id,
            "action_kind": "human_intervention",
            "boundary_facts": {
                "parent_run_id": run_id,
                "parent_run_status": "active",
            },
            "intervention_source": "gate_recommendation",
            "intervention_evidence": [{"artifact_id": "iev-1", "artifact_kind": "evidence"}],
            "authorization": _action_auth("architect"),
            "human_intent": "provide_evidence",
            "prohibited_override_facts": {
                "required_gate_override_attempted": False,
                "pass_evidence_fabricated": False,
                "retry_resume_bounds_bypassed": False,
            },
            "gate_snapshot_binding": binding,
        })
    return copy.deepcopy({
        "policy_declaration": _policy_declaration(),
        "decision_id": "act-dec-human-001",
        "evaluated_at": BASE_TIME,
        "evaluated_by": "architect",
        "run_id": run_id,
        "action_kind": "human_intervention",
        "boundary_facts": {
            "parent_run_id": run_id,
            "parent_run_status": "active",
        },
        "intervention_source": "policy_exhaustion",
        "intervention_evidence": [{"artifact_id": "iev-1", "artifact_kind": "evidence"}],
        "authorization": _action_auth("architect"),
        "human_intent": "provide_evidence",
        "prohibited_override_facts": {
            "required_gate_override_attempted": False,
            "pass_evidence_fabricated": False,
            "retry_resume_bounds_bypassed": False,
        },
        "policy_exhaustion_facts": {"exhaustion_classification": "no_permitted_action"},
    })


def _terminate_request(run_id):
    return copy.deepcopy({
        "policy_declaration": _policy_declaration(),
        "decision_id": "act-dec-terminate-001",
        "evaluated_at": BASE_TIME,
        "evaluated_by": "architect",
        "run_id": run_id,
        "action_kind": "terminate",
        "boundary_facts": {
            "parent_run_id": run_id,
            "parent_run_status": "active",
        },
        "authorization": _action_auth("architect"),
        "terminate_reason": "Conformance test termination.",
    })


# ---------------------------------------------------------------------------
# Visibility context and run.created payload (reimplemented locally)
# ---------------------------------------------------------------------------

def _visibility_context(visibility="public"):
    return {
        "trigger_visibility": {
            "contributor_id": "test-trigger-001",
            "contributor_kind": "trigger_provenance",
            "contributor_ref": {"artifact_id": "ticket-test", "artifact_kind": "ticket"},
            "asserted_visibility": visibility,
            "authority": "Test ticket trigger",
            "classification_evidence": [{"artifact_id": "ticket-test", "artifact_kind": "ticket"}],
        },
        "policy_contributors": [
            {
                "contributor_id": "test-policy-001",
                "contributor_kind": "project_policy",
                "contributor_ref": {"artifact_id": "project-policy", "artifact_kind": "policy"},
                "asserted_visibility": visibility,
                "authority": "Test project policy",
                "classification_evidence": [{"artifact_id": "project-policy", "artifact_kind": "policy"}],
            }
        ],
        "contract_contributors": [
            {
                "contributor_id": "test-contract-001",
                "contributor_kind": "governing_contract",
                "contributor_ref": {
                    "artifact_id": "runtime-state-contract",
                    "artifact_kind": "contract",
                    "artifact_version": "0.9.0",
                },
                "asserted_visibility": visibility,
                "authority": "Test governing contract",
                "classification_evidence": [
                    {"artifact_id": "runtime-state-contract", "artifact_kind": "contract", "artifact_version": "0.9.0"}
                ],
            }
        ],
        "run_visibility_resolution": {
            "resolution_id": "test-resolution-run-001",
            "resolved_at": "2026-01-01T00:00:00Z",
            "contributors": [
                {
                    "contributor_id": "test-trigger-001",
                    "contributor_kind": "trigger_provenance",
                    "contributor_ref": {"artifact_id": "ticket-test", "artifact_kind": "ticket"},
                    "asserted_visibility": visibility,
                    "authority": "Test ticket trigger",
                    "classification_evidence": [{"artifact_id": "ticket-test", "artifact_kind": "ticket"}],
                },
                {
                    "contributor_id": "test-policy-001",
                    "contributor_kind": "project_policy",
                    "contributor_ref": {"artifact_id": "project-policy", "artifact_kind": "policy"},
                    "asserted_visibility": visibility,
                    "authority": "Test project policy",
                    "classification_evidence": [{"artifact_id": "project-policy", "artifact_kind": "policy"}],
                },
                {
                    "contributor_id": "test-contract-001",
                    "contributor_kind": "governing_contract",
                    "contributor_ref": {
                        "artifact_id": "runtime-state-contract",
                        "artifact_kind": "contract",
                        "artifact_version": "0.9.0",
                    },
                    "asserted_visibility": visibility,
                    "authority": "Test governing contract",
                    "classification_evidence": [
                        {"artifact_id": "runtime-state-contract", "artifact_kind": "contract", "artifact_version": "0.9.0"}
                    ],
                },
            ],
            "resolution_rule": "most_restrictive",
            "resolved_visibility": visibility,
            "resolution_audit": {
                "contributor_count": 3,
                "restricted_count": 3 if visibility == "restricted" else 0,
                "project_count": 3 if visibility == "project" else 0,
                "public_count": 3 if visibility == "public" else 0,
                "applied_rule": "most_restrictive",
            },
        },
        "resolved_run_visibility": visibility,
    }


def _run_created_payload(visibility="public"):
    return {
        "run_provenance": {
            "origin_artifact": {"artifact_id": "conformance-001", "artifact_kind": "ticket"},
            "governing_contracts": [
                {"artifact_id": "runtime-state-contract", "artifact_kind": "contract", "artifact_version": "0.9.0"}
            ],
            "additional_sources": [],
        },
        "trigger": "ticket",
        "executor_identity": "runner-1",
        "run_ordinal": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "stage_graph": {
            "graph_id": "simple-graph",
            "stages": [
                {
                    "stage_id": STAGE_ID,
                    "name": "Build",
                    "required": True,
                    "status": "pending",
                    "gates": [
                        {
                            "gate_id": GATE_ID,
                            "gate_type": "validator",
                            "required": True,
                            "failure_behavior": "halt_run",
                            "contract_ref": {
                                "artifact_id": "test-contract",
                                "artifact_kind": "contract",
                                "artifact_version": "0.9.0",
                                "locator": "references/runtime-state-contract.md",
                            },
                        }
                    ],
                }
            ],
            "edges": [],
            "entry_stages": [STAGE_ID],
            "terminal_stages": [STAGE_ID],
        },
        "visibility_context": _visibility_context(visibility),
    }


# ---------------------------------------------------------------------------
# Append-request builder with explicit causation (causation_chain)
# ---------------------------------------------------------------------------

def _make_request(event_type, payload, run_id, prev_digest, head_order, causation_id):
    return {
        "run_id": run_id,
        "event_type": event_type,
        "payload": payload,
        "causation_chain": [causation_id],
        "actor_role": "runner",
        "actor_identity": "runner-1",
        "trigger_artifact": {"artifact_id": "ticket-test", "artifact_kind": "ticket"},
        "reason": "conformance",
        "recommended_action": "none",
        "expected_stream_head": {"event_order": head_order, "content_digest": prev_digest},
        "client_event_id": f"ce-{run_id}-{head_order}-{event_type}",
        "prev_event_digest": prev_digest,
    }


def _child_created_payload(lineage):
    payload = _run_created_payload()
    payload["lineage"] = copy.deepcopy(lineage)
    return payload


# ---------------------------------------------------------------------------
# Caller-owned action mapping (NOT a production API)
# ---------------------------------------------------------------------------

def _map_action_to_event(action_decision, run_id, child_run_id, boundary, parent_status):
    """Map an accepted RuntimeActionDecision to sidecar event spec(s).

    Returns (parent_events, child_payload) where parent_events is a list of
    {event_type, payload, causation_id} to append to the parent run, and
    child_payload is the run.created payload for a child run (or None). Every
    payload copies authoritative fields from the evaluator output and original
    request and carries an explicit causation_chain equal to the decision id.
    """
    kind = action_decision["action_kind"]
    dec_id = action_decision["decision_id"]

    if kind == "stop_stage":
        parent_events = [{
            "event_type": "run.stage.failed",
            "causation_id": dec_id,
            "payload": {
                "stage_id": STAGE_ID,
                "failed_at": BASE_TIME,
                "error": {"code": "ER_GATE_STOP_STAGE", "message": "Gate decision recommended stop_stage."},
                "failure_category": "command_failed",
                "failure_is_transient": False,
                "failure_is_deterministic": True,
                "artifacts_produced_before_failure": [],
                "retry_eligible": False,
            },
        }]
        return parent_events, None

    if kind == "stop_run":
        parent_events = [{
            "event_type": "run.failed",
            "causation_id": dec_id,
            "payload": {
                "failed_at": BASE_TIME,
                "failed_stage_id": STAGE_ID,
                "error": {"code": "ER_GATE_STOP_RUN", "message": "Gate decision recommended stop_run."},
                "failure_category": "command_failed",
                "failure_is_transient": False,
                "failure_is_deterministic": True,
                "retry_eligible": False,
            },
        }]
        return parent_events, None

    if kind == "human_intervention":
        intent = action_decision.get("human_intent", "provide_evidence")
        parent_events = [{
            "event_type": "run.intervention",
            "causation_id": dec_id,
            "payload": {
                "intervention_id": dec_id,
                "intervention_type": intent,
                "authorized_by": "architect",
                "reason": "Conformance test human intervention.",
                "evidence": [{"artifact_id": "iev-1", "artifact_kind": "evidence"}],
            },
        }]
        return parent_events, None

    if kind == "terminate":
        parent_events = [{
            "event_type": "run.terminated",
            "causation_id": dec_id,
            "payload": {
                "terminated_at": BASE_TIME,
                "terminated_by": "architect",
                "termination_reason": "Conformance test termination.",
                "from_status": "active",
                "terminal_status": "failed",
            },
        }]
        return parent_events, None

    # Child-bearing actions: retry, resume, redesign, more_evidence.
    if kind == "more_evidence":
        lineage = {
            "parent_run_id": run_id,
            "lineage_kind": "more_evidence",
            "lineage_reason": "Blocked gate decision requires additional evidence.",
            "parent_status": parent_status,
            "parent_boundary_event_id": boundary["event_id"],
            "parent_boundary_event_type": boundary["event_type"],
            "parent_boundary_event_order": boundary["event_order"],
        }
        return [], _child_created_payload(lineage)

    # retry / resume / redesign append a parent action event then create child.
    if kind == "retry":
        lineage = {
            "parent_run_id": run_id,
            "lineage_kind": "retry",
            "lineage_reason": "Gate decision recommended stop_stage; retry authorized.",
            "parent_status": parent_status,
            "parent_boundary_event_id": boundary["event_id"],
            "parent_boundary_event_type": boundary["event_type"],
            "parent_boundary_event_order": boundary["event_order"],
        }
        parent_events = [{
            "event_type": "run.retry.initiated",
            "causation_id": dec_id,
            "payload": {
                "new_run_id": child_run_id,
                "lineage": copy.deepcopy(lineage),
                "retry_strategy": "full",
                "current_retry_count": 1,
                "max_retries": 3,
                "failure_category": "command_failed",
                "authorized_by": "architect",
                "authorized_at": BASE_TIME,
            },
        }]
        return parent_events, _child_created_payload(lineage)

    if kind == "resume":
        lineage = {
            "parent_run_id": run_id,
            "lineage_kind": "resume",
            "lineage_reason": "Interrupted run resumes from checkpoint.",
            "parent_status": parent_status,
            "parent_boundary_event_id": boundary["event_id"],
            "parent_boundary_event_type": boundary["event_type"],
            "parent_boundary_event_order": boundary["event_order"],
        }
        parent_events = [{
            "event_type": "run.resumed",
            "causation_id": dec_id,
            "payload": {
                "new_run_id": child_run_id,
                "lineage": copy.deepcopy(lineage),
                "checkpoint_event_order": boundary["event_order"],
                "recovery_action": "replay_from_checkpoint",
                "authorized_by": "architect",
                "authorized_at": BASE_TIME,
            },
        }]
        return parent_events, _child_created_payload(lineage)

    if kind == "redesign":
        lineage = {
            "parent_run_id": run_id,
            "lineage_kind": "redesign",
            "lineage_reason": "Contract incomplete; redesign authorized.",
            "parent_status": parent_status,
            "parent_boundary_event_id": boundary["event_id"],
            "parent_boundary_event_type": boundary["event_type"],
            "parent_boundary_event_order": boundary["event_order"],
        }
        parent_events = [{
            "event_type": "run.redesign",
            "causation_id": dec_id,
            "payload": {
                "new_run_id": child_run_id,
                "lineage": copy.deepcopy(lineage),
                "revised_stage_graph": _run_created_payload()["stage_graph"],
                "authorized_by": "architect",
                "authorized_at": BASE_TIME,
            },
        }]
        return parent_events, _child_created_payload(lineage)

    raise ValueError(f"unmapped action kind: {kind}")


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------

class _E2ETestBase(unittest.TestCase):
    def setUp(self):
        fd, self._temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._sidecars = []

    def tearDown(self):
        for s in self._sidecars:
            try:
                if not s.closed:
                    s.close()
            except Exception:
                pass
        self._sidecars.clear()
        for _ in range(5):
            try:
                if os.path.exists(self._temp_db_path):
                    os.unlink(self._temp_db_path)
                break
            except PermissionError:
                continue

    def _new_sidecar(self):
        s = RuntimeStateSidecar(self._temp_db_path, SIGNER_KEY)
        self._sidecars.append(s)
        return s

    def _db_sha256(self):
        try:
            with open(self._temp_db_path, "rb") as f:
                return _sha256_hex(f.read())
        except FileNotFoundError:
            return None

    def _current_head(self, run_id, s):
        events = s.read_events(run_id)
        return {"event_order": len(events), "content_digest": events[-1]["content_digest"]}

    def _parent_boundary(self, s, run_id):
        events = s.read_events(run_id)
        last = events[-1]
        return {
            "event_id": last["event_id"],
            "event_type": last["event_type"],
            "event_order": last["event_order"],
        }

    def _create_root_run(self, s, run_id, causation_id):
        req = _make_request("run.created", _run_created_payload(), run_id=run_id,
                            prev_digest=ZERO_DIGEST, head_order=0, causation_id=causation_id)
        return s.create_run(req)

    def _append_simple(self, s, run_id, event_type, payload, causation_id):
        ch = self._current_head(run_id, s)
        req = _make_request(event_type, payload, run_id=run_id,
                            prev_digest=ch["content_digest"], head_order=ch["event_order"],
                            causation_id=causation_id)
        return s.append_event(req)

    # -- shared seed / append helpers (used by all test classes) --

    def _append_mapped(self, s, run_id, spec):
        ch = self._current_head(run_id, s)
        req = _make_request(spec["event_type"], spec["payload"], run_id=run_id,
                            prev_digest=ch["content_digest"], head_order=ch["event_order"],
                            causation_id=spec["causation_id"])
        s.append_event(req)

    def _seed_active_run(self, s, run_id):
        self._create_root_run(s, run_id, f"cause-root-{run_id}")
        self._append_simple(s, run_id, "run.started",
                            {"started_at": BASE_TIME, "executor_identity": "runner-1"},
                            f"cause-{run_id}-started")
        ch = self._current_head(run_id, s)
        self._append_simple(s, run_id, "run.stage.started",
                            {"stage_id": STAGE_ID, "started_at": BASE_TIME,
                             "entry_evidence": [{"artifact_id": "ev-0", "artifact_kind": "evidence"}]},
                            f"cause-{run_id}-stage-started")

    def _seed_failed_run(self, s, run_id):
        self._seed_active_run(s, run_id)
        ch = self._current_head(run_id, s)
        self._append_simple(s, run_id, "run.stage.failed",
            {
                "stage_id": STAGE_ID, "failed_at": BASE_TIME,
                "error": {"code": "TEST_FAIL", "message": "test failure"},
                "failure_category": "command_failed",
                "failure_is_transient": True, "failure_is_deterministic": True,
                "artifacts_produced_before_failure": [], "retry_eligible": True,
            },
            f"cause-{run_id}-stage-failed")

    def _seed_interrupted_run(self, s, run_id):
        self._seed_active_run(s, run_id)
        ch = self._current_head(run_id, s)
        self._append_simple(s, run_id, "run.interrupted",
            {"interrupted_at": BASE_TIME, "last_event_order": ch["event_order"],
             "interruption_cause": "session_lost", "checkpoint_available": True},
            f"cause-{run_id}-interrupted")

    def _seed_blocked_run(self, s, run_id):
        self._seed_active_run(s, run_id)
        ch = self._current_head(run_id, s)
        self._append_simple(s, run_id, "run.blocked",
            {"blocked_at": BASE_TIME, "blocked_reason": "More evidence needed.",
             "resolution_paths": ["more_evidence"],
             "required_evidence": [{"artifact_id": "ev-needed", "artifact_kind": "evidence"}]},
            f"cause-{run_id}-blocked")


# ---------------------------------------------------------------------------
# Requirement 1: Pass-gate-to-stage-completion flow
# ---------------------------------------------------------------------------

class TestPassGateToStageCompletion(_E2ETestBase):
    def test_complete_pass_gate_flow(self):
        s = self._new_sidecar()
        run_id = "run-pass-001"
        cr_receipt = self._create_root_run(s, run_id, f"cause-root-{run_id}")
        head = cr_receipt["new_stream_head"]

        self._append_simple(s, run_id, "run.started",
                            {"started_at": BASE_TIME, "executor_identity": "runner-1"},
                            f"cause-{run_id}-started")
        ch = self._current_head(run_id, s)
        self._append_simple(s, run_id, "run.stage.started",
                            {"stage_id": STAGE_ID, "started_at": BASE_TIME,
                             "entry_evidence": [{"artifact_id": "ev-0", "artifact_kind": "evidence"}]},
                            f"cause-{run_id}-stage-started")

        gd = _pass_gate_decision()
        self.assertEqual(gd["outcome"], "pass")
        self.assertEqual(gd["recommendation"], "proceed")

        ch = self._current_head(run_id, s)
        ge = _make_request("run.gate.evaluated",
            {
                "stage_id": STAGE_ID,
                "gate_id": gd["gate_id"],
                "decision_id": gd["decision_id"],
                "outcome": gd["outcome"],
                "execution_mode": gd["execution_mode"],
                "evaluated_at": gd["evaluated_at"],
                "evaluated_by": gd["evaluated_by"],
                "evidence": gd["evidence"],
            },
            run_id=run_id, prev_digest=ch["content_digest"], head_order=ch["event_order"],
            causation_id=gd["decision_id"])
        s.append_event(ge)

        ch = self._current_head(run_id, s)
        self._append_simple(s, run_id, "run.stage.completed",
            {
                "stage_id": STAGE_ID,
                "completed_at": BASE_TIME,
                "gate_decisions": [gd["decision_id"]],
                "artifacts_produced": [],
            },
            f"cause-{run_id}-stage-completed")

        events = s.read_events(run_id)
        self.assertEqual(len(events), 5)
        self.assertEqual(events[0]["event_type"], "run.created")
        self.assertEqual(events[1]["event_type"], "run.started")
        self.assertEqual(events[2]["event_type"], "run.stage.started")
        self.assertEqual(events[3]["event_type"], "run.gate.evaluated")
        self.assertEqual(events[4]["event_type"], "run.stage.completed")

        for i, ev in enumerate(events):
            self.assertEqual(ev["event_order"], i + 1)

        for i in range(1, len(events)):
            self.assertEqual(events[i]["prev_event_digest"], events[i - 1]["content_digest"])
        self.assertEqual(events[0]["prev_event_digest"], ZERO_DIGEST)

        # Gate event uses decision_id causation, and stored fields equal evaluator output.
        ge_stored = events[3]
        self.assertEqual(ge_stored["causation_chain"], [gd["decision_id"]])
        self.assertEqual(ge_stored["payload"]["outcome"], gd["outcome"])
        self.assertEqual(ge_stored["payload"]["execution_mode"], gd["execution_mode"])
        self.assertEqual(ge_stored["payload"]["gate_id"], gd["gate_id"])
        self.assertEqual(ge_stored["payload"]["decision_id"], gd["decision_id"])
        self.assertEqual(ge_stored["payload"]["evaluated_at"], gd["evaluated_at"])
        self.assertEqual(ge_stored["payload"]["evaluated_by"], gd["evaluated_by"])
        self.assertEqual(ge_stored["payload"]["evidence"], gd["evidence"])

        # Independent content-digest recomputation.
        for ev in events:
            self.assertEqual(ev["content_digest"], _event_content_digest(ev))

        # Projection digest recomputed exactly.
        proj = s.get_run(run_id)
        self.assertEqual(proj["projection_digest"], _projection_digest(proj))

        # Receipts verified independently.
        snap = s.evidence_snapshot(run_id)
        _verify_chain(snap)

        # Export digest recomputed exactly.
        envelope = s.export_evidence(run_id)
        self.assertEqual(envelope["export_content_digest"], _export_digest(envelope))

    def test_gate_decision_identity_preserved(self):
        gd = _pass_gate_decision()
        self.assertEqual(gd["outcome"], "pass")
        self.assertIn("decision_id", gd)
        self.assertIn("gate_id", gd)
        self.assertIn("evidence", gd)
        self.assertTrue(len(gd["evidence"]) > 0)


# ---------------------------------------------------------------------------
# Requirement 2: Fail-gate-to-action flow
# ---------------------------------------------------------------------------

class TestFailGateToActionFlow(_E2ETestBase):
    def test_full_fail_gate_to_failed_state(self):
        s = self._new_sidecar()
        run_id = "run-ff-001"
        self._create_root_run(s, run_id, f"cause-root-{run_id}")
        self._append_simple(s, run_id, "run.started",
                            {"started_at": BASE_TIME, "executor_identity": "runner-1"},
                            f"cause-{run_id}-started")
        ch = self._current_head(run_id, s)
        self._append_simple(s, run_id, "run.stage.started",
                            {"stage_id": STAGE_ID, "started_at": BASE_TIME,
                             "entry_evidence": [{"artifact_id": "ev-0", "artifact_kind": "evidence"}]},
                            f"cause-{run_id}-stage-started")

        gd = _fail_gate_decision_stop_stage()
        ch = self._current_head(run_id, s)
        ge = _make_request("run.gate.evaluated",
            {
                "stage_id": STAGE_ID, "gate_id": gd["gate_id"], "decision_id": gd["decision_id"],
                "outcome": gd["outcome"], "execution_mode": gd["execution_mode"],
                "evaluated_at": gd["evaluated_at"], "evaluated_by": gd["evaluated_by"],
                "evidence": gd["evidence"],
            },
            run_id=run_id, prev_digest=ch["content_digest"], head_order=ch["event_order"],
            causation_id=gd["decision_id"])
        s.append_event(ge)

        action_request = _stop_stage_request(run_id)
        action_decision = eval_action(action_request)
        self.assertEqual(action_decision["disposition"], "authorized")
        self.assertEqual(action_decision["reason_code"], "action_authorized_stop_stage")

        parent_events, _ = _map_action_to_event(action_decision, run_id, None, None, None)
        spec = parent_events[0]
        ch = self._current_head(run_id, s)
        req = _make_request(spec["event_type"], spec["payload"], run_id=run_id,
                            prev_digest=ch["content_digest"], head_order=ch["event_order"],
                            causation_id=spec["causation_id"])
        s.append_event(req)

        events = s.read_events(run_id)
        self.assertEqual(events[-1]["event_type"], "run.stage.failed")
        for i in range(1, len(events)):
            self.assertEqual(events[i]["prev_event_digest"], events[i - 1]["content_digest"])
        self.assertEqual(events[0]["prev_event_digest"], ZERO_DIGEST)
        self.assertEqual(events[-1]["causation_chain"], [action_decision["decision_id"]])

        self.assertEqual(s.get_stage(run_id, STAGE_ID)["status"], "failed")
        self.assertEqual(s.get_run(run_id)["status"], "failed")


# ---------------------------------------------------------------------------
# Requirement 3: All eight authorized action mappings through the real sidecar
# ---------------------------------------------------------------------------

class TestAllActionMappings(_E2ETestBase):
    # Eight authorized action mappings are driven end-to-end against the real
    # sidecar. The five single-event mappings (stop_stage, stop_run,
    # human_intervention, terminate) and the more_evidence child genesis append
    # and verify cleanly. The three child-bearing action events
    # (run.retry.initiated, run.resumed, run.redesign) are driven as REAL
    # successful end-to-end flows: the parent lineage action is appended through
    # the live sidecar (proving the journal write-path now binds store-assigned
    # event metadata so the reducer's lineage-boundary check passes), the
    # stored next_state carries the bound latest_event_* metadata, and the child
    # run is created with complete lineage and decision_id causation.

    def test_stop_stage_mapping(self):
        s = self._new_sidecar()
        run_id = "run-act-stop-stage-001"
        self._seed_active_run(s, run_id)
        action_decision = eval_action(_stop_stage_request(run_id))
        self.assertEqual(action_decision["disposition"], "authorized")
        self.assertEqual(action_decision["reason_code"], "action_authorized_stop_stage")
        parent_events, _ = _map_action_to_event(action_decision, run_id, None, None, None)
        self.assertEqual(parent_events[0]["event_type"], "run.stage.failed")
        self._append_mapped(s, run_id, parent_events[0])
        self.assertEqual(s.get_stage(run_id, STAGE_ID)["status"], "failed")

    def test_stop_run_mapping(self):
        s = self._new_sidecar()
        run_id = "run-act-stop-run-001"
        self._seed_active_run(s, run_id)
        action_decision = eval_action(_stop_run_request(run_id))
        self.assertEqual(action_decision["disposition"], "authorized")
        self.assertEqual(action_decision["reason_code"], "action_authorized_stop_run")
        parent_events, _ = _map_action_to_event(action_decision, run_id, None, None, None)
        self.assertEqual(parent_events[0]["event_type"], "run.failed")
        self._append_mapped(s, run_id, parent_events[0])
        self.assertEqual(s.get_run(run_id)["status"], "failed")

    def test_retry_mapping(self):
        s = self._new_sidecar()
        run_id = "run-act-retry-001"
        child_id = "child-retry-001"
        self._seed_failed_run(s, run_id)
        boundary = self._parent_boundary(s, run_id)
        action_decision = eval_action(_retry_request(run_id, child_id))
        self.assertEqual(action_decision["disposition"], "authorized")
        self.assertEqual(action_decision["reason_code"], "action_authorized_retry")
        parent_events, child_payload = _map_action_to_event(action_decision, run_id, child_id, boundary, "failed")
        self.assertEqual(parent_events[0]["event_type"], "run.retry.initiated")
        # Real successful end-to-end: the parent lineage action now appends
        # through the live sidecar because the journal write-path binds the
        # store-assigned latest_event_* metadata the reducer boundary check needs.
        before = len(s.read_events(run_id))
        self._append_mapped(s, run_id, parent_events[0])
        after = s.read_events(run_id)
        self.assertEqual(len(after), before + 1)
        parent_event = after[-1]
        self.assertEqual(parent_event["event_type"], "run.retry.initiated")
        self.assertEqual(parent_event["causation_chain"], [action_decision["decision_id"]])
        # Stored next_state carries the bound latest event metadata (proof of fix).
        ns = parent_event["next_state"]
        self.assertEqual(ns["latest_event_id"], parent_event["event_id"])
        self.assertEqual(ns["latest_event_type"], "run.retry.initiated")
        self.assertEqual(ns["latest_event_order"], parent_event["event_order"])
        # Lineage references the exact boundary event identity.
        lineage = parent_event["payload"]["lineage"]
        self.assertEqual(lineage["parent_boundary_event_id"], boundary["event_id"])
        self.assertEqual(lineage["parent_boundary_event_type"], boundary["event_type"])
        self.assertEqual(lineage["parent_boundary_event_order"], boundary["event_order"])
        # Child genesis with complete lineage and decision_id causation.
        child_req = _make_request("run.created", child_payload, run_id=child_id,
                                  prev_digest=ZERO_DIGEST, head_order=0,
                                  causation_id=action_decision["decision_id"])
        s.create_run(child_req)
        child_events = s.read_events(child_id)
        self.assertEqual(len(child_events), 1)
        self.assertEqual(child_events[0]["event_order"], 1)
        self.assertEqual(child_events[0]["payload"]["lineage"]["lineage_kind"], "retry")
        self.assertEqual(child_events[0]["payload"]["lineage"]["parent_run_id"], run_id)
        self.assertEqual(child_events[0]["causation_chain"], [action_decision["decision_id"]])
        self.assertEqual(s.get_run(run_id)["status"], "failed")

    def test_resume_mapping(self):
        s = self._new_sidecar()
        run_id = "run-act-resume-001"
        child_id = "child-resume-001"
        self._seed_interrupted_run(s, run_id)
        boundary = self._parent_boundary(s, run_id)
        action_decision = eval_action(_resume_request(run_id, child_id))
        self.assertEqual(action_decision["disposition"], "authorized")
        self.assertEqual(action_decision["reason_code"], "action_authorized_resume")
        parent_events, child_payload = _map_action_to_event(action_decision, run_id, child_id, boundary, "interrupted")
        self.assertEqual(parent_events[0]["event_type"], "run.resumed")
        before = len(s.read_events(run_id))
        self._append_mapped(s, run_id, parent_events[0])
        after = s.read_events(run_id)
        self.assertEqual(len(after), before + 1)
        parent_event = after[-1]
        self.assertEqual(parent_event["event_type"], "run.resumed")
        self.assertEqual(parent_event["causation_chain"], [action_decision["decision_id"]])
        ns = parent_event["next_state"]
        self.assertEqual(ns["latest_event_id"], parent_event["event_id"])
        self.assertEqual(ns["latest_event_type"], "run.resumed")
        self.assertEqual(ns["latest_event_order"], parent_event["event_order"])
        lineage = parent_event["payload"]["lineage"]
        self.assertEqual(lineage["parent_boundary_event_id"], boundary["event_id"])
        self.assertEqual(lineage["parent_boundary_event_type"], boundary["event_type"])
        self.assertEqual(lineage["parent_boundary_event_order"], boundary["event_order"])
        child_req = _make_request("run.created", child_payload, run_id=child_id,
                                  prev_digest=ZERO_DIGEST, head_order=0,
                                  causation_id=action_decision["decision_id"])
        s.create_run(child_req)
        child_events = s.read_events(child_id)
        self.assertEqual(child_events[0]["event_order"], 1)
        self.assertEqual(child_events[0]["payload"]["lineage"]["lineage_kind"], "resume")
        self.assertEqual(child_events[0]["payload"]["lineage"]["parent_run_id"], run_id)
        self.assertEqual(child_events[0]["causation_chain"], [action_decision["decision_id"]])
        self.assertEqual(s.get_run(run_id)["status"], "interrupted")

    def test_more_evidence_mapping(self):
        s = self._new_sidecar()
        run_id = "run-act-me-001"
        child_id = "child-me-001"
        self._seed_blocked_run(s, run_id)
        boundary = self._parent_boundary(s, run_id)
        action_decision = eval_action(_more_evidence_request(run_id))
        self.assertEqual(action_decision["disposition"], "authorized")
        self.assertEqual(action_decision["reason_code"], "action_authorized_more_evidence")
        parent_events, child_payload = _map_action_to_event(action_decision, run_id, child_id, boundary, "blocked")
        self.assertEqual(parent_events, [])
        # Child genesis with complete lineage (not a plain root payload).
        child_req = _make_request("run.created", child_payload, run_id=child_id,
                                  prev_digest=ZERO_DIGEST, head_order=0,
                                  causation_id=action_decision["decision_id"])
        s.create_run(child_req)
        child_events = s.read_events(child_id)
        self.assertEqual(child_events[0]["event_type"], "run.created")
        lineage = child_events[0]["payload"]["lineage"]
        self.assertEqual(lineage["lineage_kind"], "more_evidence")
        self.assertEqual(lineage["parent_run_id"], run_id)
        self.assertEqual(lineage["parent_status"], "blocked")
        self.assertEqual(lineage["parent_boundary_event_type"], "run.blocked")
        self.assertEqual(child_events[0]["causation_chain"], [action_decision["decision_id"]])
        self.assertEqual(s.get_run(run_id)["status"], "blocked")

    def test_redesign_mapping(self):
        s = self._new_sidecar()
        run_id = "run-act-rd-001"
        child_id = "child-rd-001"
        self._seed_failed_run(s, run_id)
        boundary = self._parent_boundary(s, run_id)
        action_decision = eval_action(_redesign_request(run_id))
        self.assertEqual(action_decision["disposition"], "authorized")
        self.assertEqual(action_decision["reason_code"], "action_authorized_redesign")
        parent_events, child_payload = _map_action_to_event(action_decision, run_id, child_id, boundary, "failed")
        self.assertEqual(parent_events[0]["event_type"], "run.redesign")
        before = len(s.read_events(run_id))
        self._append_mapped(s, run_id, parent_events[0])
        after = s.read_events(run_id)
        self.assertEqual(len(after), before + 1)
        parent_event = after[-1]
        self.assertEqual(parent_event["event_type"], "run.redesign")
        self.assertEqual(parent_event["causation_chain"], [action_decision["decision_id"]])
        ns = parent_event["next_state"]
        self.assertEqual(ns["latest_event_id"], parent_event["event_id"])
        self.assertEqual(ns["latest_event_type"], "run.redesign")
        self.assertEqual(ns["latest_event_order"], parent_event["event_order"])
        lineage = parent_event["payload"]["lineage"]
        self.assertEqual(lineage["parent_boundary_event_id"], boundary["event_id"])
        self.assertEqual(lineage["parent_boundary_event_type"], boundary["event_type"])
        self.assertEqual(lineage["parent_boundary_event_order"], boundary["event_order"])
        child_req = _make_request("run.created", child_payload, run_id=child_id,
                                  prev_digest=ZERO_DIGEST, head_order=0,
                                  causation_id=action_decision["decision_id"])
        s.create_run(child_req)
        child_events = s.read_events(child_id)
        self.assertEqual(child_events[0]["event_order"], 1)
        self.assertEqual(child_events[0]["payload"]["lineage"]["lineage_kind"], "redesign")
        self.assertEqual(child_events[0]["payload"]["lineage"]["parent_run_id"], run_id)
        self.assertEqual(child_events[0]["causation_chain"], [action_decision["decision_id"]])
        self.assertEqual(s.get_run(run_id)["status"], "failed")

    def test_human_intervention_mapping(self):
        s = self._new_sidecar()
        run_id = "run-act-human-001"
        self._seed_active_run(s, run_id)
        action_decision = eval_action(_human_intervention_request(run_id))
        self.assertEqual(action_decision["disposition"], "authorized")
        self.assertEqual(action_decision["reason_code"], "action_authorized_human_intervention")
        parent_events, _ = _map_action_to_event(action_decision, run_id, None, None, None)
        self.assertEqual(parent_events[0]["event_type"], "run.intervention")
        self._append_mapped(s, run_id, parent_events[0])
        events = s.read_events(run_id)
        self.assertEqual(events[-1]["event_type"], "run.intervention")
        self.assertEqual(events[-1]["causation_chain"], [action_decision["decision_id"]])

    def test_terminate_mapping(self):
        s = self._new_sidecar()
        run_id = "run-act-term-001"
        self._seed_active_run(s, run_id)
        action_decision = eval_action(_terminate_request(run_id))
        self.assertEqual(action_decision["disposition"], "authorized")
        self.assertEqual(action_decision["reason_code"], "action_authorized_terminate")
        parent_events, _ = _map_action_to_event(action_decision, run_id, None, None, None)
        self.assertEqual(parent_events[0]["event_type"], "run.terminated")
        self._append_mapped(s, run_id, parent_events[0])
        events = s.read_events(run_id)
        self.assertEqual(events[-1]["event_type"], "run.terminated")
        self.assertEqual(events[-1]["causation_chain"], [action_decision["decision_id"]])


# ---------------------------------------------------------------------------
# Requirement 4: Zero-write probes (exact error type, no swallowed exception)
# ---------------------------------------------------------------------------

class TestZeroWriteProbes(_E2ETestBase):
    def test_denied_action_appends_nothing(self):
        s = self._new_sidecar()
        run_id = "run-zw-denied-001"
        self._seed_active_run(s, run_id)
        dec_id = "act-dec-denied-001"
        denied_request = copy.deepcopy(_stop_stage_request(run_id))
        denied_request["boundary_facts"]["parent_run_status"] = "completed"
        denied_request["decision_id"] = dec_id

        before_count = len(s.read_events(run_id))
        before_db_hash = self._db_sha256()
        before_proj = s.get_run(run_id)

        action_result = eval_action(denied_request)
        self.assertEqual(action_result["disposition"], "denied")
        self.assertEqual(action_result["reason_code"], "denied_parent_status_ineligible")

        after_count = len(s.read_events(run_id))
        after_db_hash = self._db_sha256()
        after_proj = s.get_run(run_id)
        self.assertEqual(before_count, after_count)
        self.assertEqual(before_db_hash, after_db_hash)
        self.assertEqual(before_proj["projection_digest"], after_proj["projection_digest"])

    def test_action_evaluation_error_mutates_nothing(self):
        s = self._new_sidecar()
        run_id = "run-zw-action-err-001"
        self._seed_active_run(s, run_id)
        bad_request = copy.deepcopy(_stop_stage_request(run_id))
        bad_request["action_kind"] = "bogus_action"
        before_count = len(s.read_events(run_id))
        before_db_hash = self._db_sha256()
        result = eval_action(bad_request)
        self.assertIn("error_code", result)
        self.assertEqual(result["error_code"], "unknown_action_kind")
        after_count = len(s.read_events(run_id))
        after_db_hash = self._db_sha256()
        self.assertEqual(before_count, after_count)
        self.assertEqual(before_db_hash, after_db_hash)

    def test_gate_evaluation_error_mutates_nothing(self):
        s = self._new_sidecar()
        run_id = "run-zw-gate-err-001"
        self._seed_active_run(s, run_id)
        bad_request = _gate_request("pass", decision_id="gate-dec-err-001")
        bad_request.pop("decision_id")
        before_count = len(s.read_events(run_id))
        before_db_hash = self._db_sha256()
        result = evaluate_gate(bad_request)
        self.assertIn("error_code", result)
        self.assertEqual(result["error_code"], "invalid_input_branch")
        after_count = len(s.read_events(run_id))
        after_db_hash = self._db_sha256()
        self.assertEqual(before_count, after_count)
        self.assertEqual(before_db_hash, after_db_hash)

    def test_tampered_gate_binding_mutates_nothing(self):
        s = self._new_sidecar()
        run_id = "run-zw-tamper-001"
        self._seed_active_run(s, run_id)
        action_request = copy.deepcopy(_stop_stage_request(run_id))
        action_request["gate_snapshot_binding"]["canonical_digest"] = "sha256:" + "0" * 64
        before_count = len(s.read_events(run_id))
        before_db_hash = self._db_sha256()
        result = eval_action(action_request)
        self.assertIn("error_code", result)
        self.assertEqual(result["error_code"], "gate_snapshot_contradiction")
        after_count = len(s.read_events(run_id))
        after_db_hash = self._db_sha256()
        self.assertEqual(before_count, after_count)
        self.assertEqual(before_db_hash, after_db_hash)

    def test_state_invalid_mapping_rejected_exact_error(self):
        s = self._new_sidecar()
        run_id = "run-zw-invalid-001"
        self._create_root_run(s, run_id, f"cause-root-{run_id}")
        before_count = len(s.read_events(run_id))
        ch = self._current_head(run_id, s)
        bad_req = _make_request("run.stage.failed",
            {"stage_id": STAGE_ID, "failed_at": BASE_TIME,
             "error": {"code": "BAD_MAP", "message": "bad mapping"},
             "failure_category": "command_failed", "failure_is_transient": False,
             "failure_is_deterministic": True, "artifacts_produced_before_failure": [],
             "retry_eligible": False},
            run_id=run_id, prev_digest=ch["content_digest"], head_order=ch["event_order"],
            causation_id="cause-bad-map-001")
        with self.assertRaises(ValueError) as ctx:
            s.append_event(bad_req)
        self.assertIsInstance(ctx.exception, ValueError)
        self.assertEqual(str(ctx.exception), "reducer-prior-status")
        after_count = len(s.read_events(run_id))
        self.assertEqual(before_count, after_count, "Event count changed after invalid mapping")


# ---------------------------------------------------------------------------
# Requirement 5: State-invalid caller mapping rejected (covered above)
# ---------------------------------------------------------------------------
# Requirement 6: Determinism and immutability
# ---------------------------------------------------------------------------

class TestDeterminismAndImmutability(_E2ETestBase):
    def test_delayed_repeat_gate_evaluations_byte_equivalent(self):
        req = _gate_request("fail", decision_id="gate-dec-det-001")
        a = evaluate_gate(copy.deepcopy(req))
        b = evaluate_gate(copy.deepcopy(req))
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_delayed_repeat_action_evaluations_byte_equivalent(self):
        s = self._new_sidecar()
        run_id = "run-det-001"
        self._seed_active_run(s, run_id)
        req = _human_intervention_request(run_id)
        a = eval_action(copy.deepcopy(req))
        b = eval_action(copy.deepcopy(req))
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_mutating_evaluator_output_does_not_mutate_request(self):
        req = _gate_request("pass", decision_id="gate-dec-mut-001")
        before = copy.deepcopy(req)
        result = evaluate_gate(copy.deepcopy(req))
        result["evidence"][0]["artifact_id"] = "mutated"
        self.assertEqual(req, before)
        result2 = evaluate_gate(copy.deepcopy(req))
        self.assertEqual(result2["evidence"][0]["artifact_id"], EVIDENCE_REF["artifact_id"])

    def test_repeat_does_not_change_sidecar_state(self):
        s = self._new_sidecar()
        run_id = "run-det-repeat-001"
        self._seed_active_run(s, run_id)
        req = _human_intervention_request(run_id)
        before_count = len(s.read_events(run_id))
        for _ in range(3):
            eval_action(copy.deepcopy(req))
        after_count = len(s.read_events(run_id))
        self.assertEqual(before_count, after_count)


# ---------------------------------------------------------------------------
# Requirement 7: Independent cryptographic verification (stdlib only)
# ---------------------------------------------------------------------------

class TestIndependentCryptoVerification(_E2ETestBase):
    def test_canonical_gate_digest_recomputed(self):
        gd = _pass_gate_decision()
        binding = _make_gate_binding(gd)
        recomputed = _compute_canonical_gate_digest(binding["gate_decision_snapshot"])
        self.assertEqual(binding["canonical_digest"], recomputed)
        self.assertEqual(binding["source_gate_decision_ref"]["digest"], recomputed)

    def test_event_content_digests_recomputed(self):
        s = self._new_sidecar()
        run_id = "run-dig-001"
        self._create_root_run(s, run_id, f"cause-root-{run_id}")
        events = s.read_events(run_id)
        ev = events[0]
        # Independent recomputation equals stored digest exactly.
        self.assertEqual(ev["content_digest"], _event_content_digest(ev))
        # Shape is a well-formed sha256 hex string.
        self.assertTrue(ev["content_digest"].startswith("sha256:"))
        self.assertEqual(len(ev["content_digest"]), 71)

    def test_prev_digest_chain_verified(self):
        s = self._new_sidecar()
        run_id = "run-ch-001"
        self._create_root_run(s, run_id, f"cause-root-{run_id}")
        ch = self._current_head(run_id, s)
        self._append_simple(s, run_id, "run.started",
                            {"started_at": BASE_TIME, "executor_identity": "runner-1"},
                            f"cause-{run_id}-started")
        events = s.read_events(run_id)
        self.assertGreaterEqual(len(events), 2)
        for i in range(1, len(events)):
            self.assertEqual(events[i]["prev_event_digest"], events[i - 1]["content_digest"])
        self.assertEqual(events[0]["prev_event_digest"], ZERO_DIGEST)

    def test_hmac_receipt_verified(self):
        s = self._new_sidecar()
        run_id = "run-hmac-001"
        cr_receipt = self._create_root_run(s, run_id, f"cause-root-{run_id}")
        self.assertIn("signed_receipt", cr_receipt)
        signed = cr_receipt["signed_receipt"]
        self.assertEqual(signed["algorithm"], "HMAC-SHA256")
        self.assertEqual(signed["key_id"], "conformance-key-1")
        self.assertTrue(_verify_receipt_signature(cr_receipt, SIGNER_KEY))
        # Tampered signature fails verification.
        tampered = copy.deepcopy(cr_receipt)
        tampered["signed_receipt"] = copy.deepcopy(signed)
        tampered["signed_receipt"]["signature"] = "0" * 64
        self.assertFalse(_verify_receipt_signature(tampered, SIGNER_KEY))

    def test_projection_digest_verified(self):
        s = self._new_sidecar()
        run_id = "run-pd-001"
        self._create_root_run(s, run_id, f"cause-root-{run_id}")
        ch = self._current_head(run_id, s)
        self._append_simple(s, run_id, "run.started",
                            {"started_at": BASE_TIME, "executor_identity": "runner-1"},
                            f"cause-{run_id}-started")
        proj = s.get_run(run_id)
        # Exact equality; recomputed independently.
        self.assertEqual(proj["projection_digest"], _projection_digest(proj))

    def test_export_digest_verified(self):
        s = self._new_sidecar()
        run_id = "run-ed-001"
        self._create_root_run(s, run_id, f"cause-root-{run_id}")
        ch = self._current_head(run_id, s)
        self._append_simple(s, run_id, "run.started",
                            {"started_at": BASE_TIME, "executor_identity": "runner-1"},
                            f"cause-{run_id}-started")
        envelope = s.export_evidence(run_id)
        self.assertIn("export_content_digest", envelope)
        self.assertEqual(envelope["export_content_digest"], _export_digest(envelope))

    def test_chain_oracle_non_ascii_vector(self):
        # Independent canonicalization with a non-ASCII artifact id (ASCII source).
        sample = {"id": " artefact-caf\u00e9-\u65e5\u672c\u8a9e", "n": 12, "list": [True, None, "\u03bb"]}
        canonical = _oracle_canonical(sample)
        # ensure_ascii disabled -> non-ASCII emitted verbatim, not escaped.
        self.assertIn("caf\u00e9".encode("utf-8"), canonical)
        self.assertIn("\u65e5\u672c\u8a9e".encode("utf-8"), canonical)
        self.assertIn("\u03bb".encode("utf-8"), canonical)
        recomputed = "sha256:" + _sha256_hex(canonical)
        expected = _digest(sample)
        self.assertEqual(recomputed, expected)


# ---------------------------------------------------------------------------
# Requirement 8: Forbidden-import + authority scan (self-checks on this file)
# ---------------------------------------------------------------------------

class TestForbiddenImportAndAuthorityScan(unittest.TestCase):
    TEST_PATH = ROOT / "scripts" / "test_runtime_gate_action_sidecar_e2e.py"

    FORBIDDEN_IMPORTS = (
        "runtime_state_core",
        "runtime_state_projection",
        "runtime_evidence_export",
        "test_runtime_state_journal",
        "canonical_serialize",
        "compute_digest",
        "compute_event_digest",
        "unittest.mock",
        "pytest",
        "uuid",
        "random",
        "subprocess",
    )

    FORBIDDEN_TOKENS = (
        "import time",
        "canonical_serialize",
        "compute_digest",
        "compute_event_digest",
        "test_runtime_state_journal",
        "skipTest",
        "xfail",
        "fallback",
        "may pass",
        "best-effort",
    )

    def test_no_forbidden_imports(self):
        text = self.TEST_PATH.read_text(encoding="utf-8")
        for token in self.FORBIDDEN_IMPORTS:
            self.assertNotIn(f"import {token}", text)
            self.assertNotIn(f"from {token}", text)

    def test_no_forbidden_tokens(self):
        text = self.TEST_PATH.read_text(encoding="utf-8")
        # Only scan the actual test/oracle code. This self-scan class
        # definition necessarily contains every forbidden token as a literal
        # string, so exclude it from the scan to avoid self-reference.
        cut = text.split("class TestForbiddenImportAndAuthorityScan", 1)[0]
        for token in self.FORBIDDEN_TOKENS:
            self.assertNotIn(token, cut)

    def test_decision_modules_no_sqlite_import(self):
        for rel_path in ("scripts/runtime_gate_decision.py", "scripts/runtime_action_policy.py"):
            source = (ROOT / rel_path).read_text(encoding="utf-8")
            self.assertNotIn("sqlite3", source)
            self.assertNotIn("import sqlite", source)


# ---------------------------------------------------------------------------
# Requirement 9: Public hygiene (ASCII-safe, no ticket ids, control paths)
# ---------------------------------------------------------------------------

class TestPublicHygiene(unittest.TestCase):
    TEST_PATH = ROOT / "scripts" / "test_runtime_gate_action_sidecar_e2e.py"

    def test_file_is_ascii(self):
        data = self.TEST_PATH.read_bytes()
        try:
            data.decode("ascii")
        except UnicodeDecodeError:
            self.fail("Non-ASCII content in test file")

    def test_no_ticket_ids_or_control_paths(self):
        text = self.TEST_PATH.read_text(encoding="utf-8")
        # Only scan the actual test code. This self-scan class definition
        # necessarily contains the forbidden tokens as literal strings.
        cut = text.split("class TestPublicHygiene", 1)[0]
        forbidden = ["SYSTEM-", "railyard-control", "Railyard-Control", "E026", "ticket-test-001"]
        for token in forbidden:
            self.assertNotIn(token, cut)

    def test_no_secrets_or_agent_brands(self):
        for rel_path in ("scripts/runtime_gate_decision.py", "scripts/runtime_action_policy.py"):
            text = (ROOT / rel_path).read_text(encoding="utf-8").lower()
            for token in ("api_key", "anthropic", "openai", "gemini", "claude", "gpt-"):
                self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
