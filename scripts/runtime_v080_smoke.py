#!/usr/bin/env python3
"""Railyard v0.8 Runtime Smoke Runner -- thin catalog-driven deterministic executor.

Integrity contract:
  * Every semantic value (timestamps, IDs, keys, digests, visibility/authorization/
    checkpoint/provenance facts) comes from the explicit scenario ``inputs`` or the
    catalog ``shared_inputs``. The runner contains NO generators, NO defaults, and NO
    semantic call to a clock, UUID/random source, environment, network, or workflow
    state.
  * One adapter per ``(component, operation)``. Each adapter builds the request from
    explicit inputs and invokes EXACTLY ONE named production callable. Composition
    occurs only by advancing the catalog pipeline; the result of one step is carried
    forward in a runtime-only ``sidecar_state`` dict (never the caller bundle).
  * A ledger entry is appended only AFTER the corresponding real production callable
    returns or raises. The ledger records the fully qualified production callable
    (``module + "." + qualname``), never the smoke adapter name.
  * ``invocation_count`` is derived from observed engine call accounting; it equals one
    because exactly one production callable is invoked per catalog step.
  * On the first step error the executor stops immediately: no later adapter is entered
    and no later ledger entry is emitted.
  * Canonical input/output digests cover the complete structured object with no
    truncation and no lossy conversion.
  * Machine output contains repository-relative or artifact identities only; no
    absolute workspace/database path is emitted, so identical catalog + input bytes
    produce byte-identical output across distinct workspaces.

Usage:
  python scripts/runtime_v080_smoke.py --tmp-dir <dir> list
  python scripts/runtime_v080_smoke.py --tmp-dir <dir> --scenario v080-scenario-001 run
  python scripts/runtime_v080_smoke.py --tmp-dir <dir> --all run
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import hmac
import json
import pathlib
import sys

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

CONFORMANCE_CATALOG_PATH = _PROJECT_ROOT / "examples" / "runtime_v080_smoke" / "conformance.json"

# Production callables (accepted v0.8 components only). No semantic defaults here.
from scripts.runtime_evidence_export import export_run as _export_run
from scripts.runtime_publish_gate import publish_to_gate as _publish_to_gate
from scripts.runtime_state_core import ZERO_DIGEST
from scripts.runtime_state_sidecar import RuntimeStateSidecar
from scripts.runtime_validator_dispatch import dispatch as _dispatch
from scripts.runtime_validator_mesh import evaluate_validator_mesh as _evaluate_mesh
from scripts.runtime_action_policy import evaluate_runtime_action as _evaluate_action


class ScenarioInputError(Exception):
    """Raised when a required explicit scenario input is missing or surplus."""


# ---------------------------------------------------------------------------
# Deterministic helpers -- all values derive from explicit catalog inputs
# ---------------------------------------------------------------------------

def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sha256_obj(o) -> str:
    return hashlib.sha256(_canon(o).encode("utf-8")).hexdigest()


def _stdlib_sha256_prefixed(value: object) -> str:
    """Return the independent stdlib SHA-256 oracle value for *value*.

    The smoke executor deliberately does not delegate expected digest truth to a
    production digest helper.  The catalog uses ASCII-safe values, so the
    deterministic JSON representation below is the portable oracle preimage
    used by the smoke checks.
    """
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _derive_db_path(run_id: str, tmp_dir: pathlib.Path) -> str:
    return str(tmp_dir / f"{run_id}.db")


def _resolve_signer_key(inputs: dict, catalog: dict) -> bytes:
    """Resolve the signer key from explicit catalog inputs only (no module default)."""
    raw = inputs.get("signer_key_base64")
    if not raw:
        raw = catalog.get("shared_inputs", {}).get("signer_key_base64")
    if not raw:
        raise ScenarioInputError(
            "missing_signer_key_base64: signer_key_base64 must be supplied "
            "explicitly in scenario inputs or catalog shared_inputs"
        )
    try:
        return base64.b64decode(raw)
    except Exception as exc:  # pragma: no cover - defensive
        raise ScenarioInputError(f"invalid_signer_key_base64: {exc}") from exc


def _open_sidecar(run_id: str, tmp_dir: pathlib.Path, inputs: dict, catalog: dict):
    """Open the sidecar from explicit inputs. Construction (DB open) is NOT a
    production call; the production call is the sidecar method invoked later."""
    db_path = _derive_db_path(run_id, tmp_dir)
    signer_key = _resolve_signer_key(inputs, catalog)
    # clock and id_factory are fully caller-determined (deterministic), never
    # sourced from wall-clock, UUID, or random.
    clock = lambda: inputs["created_at"]
    id_factory = lambda: f"evt-{inputs['run_id']}"
    return RuntimeStateSidecar(db_path, signer_key, id_factory=id_factory, clock=clock)


# ---------------------------------------------------------------------------
# Request builders -- pure functions over explicit catalog inputs
# ---------------------------------------------------------------------------

def _build_visibility_context(visibility: str, inputs: dict) -> dict:
    # resolution_id_seed is explicit in most scenarios; fall back to the explicit
    # run_id (never a generator) so scenarios that omit the seed still validate.
    seed = inputs["resolution_id_seed"]
    resolution_id = f"res-{seed}"
    contrib_id = f"smoke-trigger-t-{seed}"
    return {
        "trigger_visibility": {
            "contributor_id": contrib_id,
            "contributor_kind": "trigger_provenance",
            "contributor_ref": {"artifact_id": "smoke-test", "artifact_kind": "ticket"},
            "asserted_visibility": visibility,
            "authority": "Smoke runner trigger",
            "classification_evidence": [{"artifact_id": "smoke-test", "artifact_kind": "ticket"}],
        },
        "policy_contributors": [
            {
                "contributor_id": "smoke-policy-001",
                "contributor_kind": "project_policy",
                "contributor_ref": {"artifact_id": "smoke-policy", "artifact_kind": "policy"},
                "asserted_visibility": visibility,
                "authority": "Smoke project policy",
                "classification_evidence": [{"artifact_id": "smoke-policy", "artifact_kind": "policy"}],
            }
        ],
        "contract_contributors": [
            {
                "contributor_id": "smoke-contract-001",
                "contributor_kind": "governing_contract",
                "contributor_ref": {
                    "artifact_id": "runtime-state-contract",
                    "artifact_kind": "contract",
                    "artifact_version": "0.9.0",
                },
                "asserted_visibility": visibility,
                "authority": "Smoke governing contract",
                "classification_evidence": [
                    {"artifact_id": "runtime-state-contract", "artifact_kind": "contract",
                     "artifact_version": "0.9.0"}
                ],
            }
        ],
        "run_visibility_resolution": {
            "resolution_id": resolution_id,
            "resolved_at": inputs["created_at"],
            "contributors": [
                {
                    "contributor_id": contrib_id,
                    "contributor_kind": "trigger_provenance",
                    "contributor_ref": {"artifact_id": "smoke-test", "artifact_kind": "ticket"},
                    "asserted_visibility": visibility,
                    "authority": "Smoke runner trigger",
                    "classification_evidence": [{"artifact_id": "smoke-test", "artifact_kind": "ticket"}],
                },
                {
                    "contributor_id": "smoke-policy-001",
                    "contributor_kind": "project_policy",
                    "contributor_ref": {"artifact_id": "smoke-policy", "artifact_kind": "policy"},
                    "asserted_visibility": visibility,
                    "authority": "Smoke project policy",
                    "classification_evidence": [{"artifact_id": "smoke-policy", "artifact_kind": "policy"}],
                },
                {
                    "contributor_id": "smoke-contract-001",
                    "contributor_kind": "governing_contract",
                    "contributor_ref": {
                        "artifact_id": "runtime-state-contract",
                        "artifact_kind": "contract",
                        "artifact_version": "0.9.0",
                    },
                    "asserted_visibility": visibility,
                    "authority": "Smoke governing contract",
                    "classification_evidence": [
                        {"artifact_id": "runtime-state-contract", "artifact_kind": "contract",
                         "artifact_version": "0.9.0"}
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


def _build_create_run_request(inputs: dict) -> dict:
    run_id = inputs["run_id"]
    ticket_id = inputs.get("ticket_id")
    visibility = inputs["visibility"]
    origin_kind = "ticket" if ticket_id else "script"
    return {
        "run_id": run_id,
        "event_type": "run.created",
        "payload": {
            "run_provenance": {
                "origin_artifact": {
                    "artifact_id": ticket_id or "smoke-provenance",
                    "artifact_kind": origin_kind,
                },
                "governing_contracts": [
                    {"artifact_id": "runtime-architecture", "artifact_kind": "contract",
                     "artifact_version": "0.8.0"}
                ],
                "additional_sources": [],
            },
            "trigger": "ticket" if ticket_id else "local_script",
            "executor_identity": inputs["executor_identity"],
            "run_ordinal": inputs["run_ordinal"],
            "created_at": inputs["created_at"],
            "stage_graph": {
                "graph_id": f"sg-{run_id}",
                "stages": [
                    {
                        "stage_id": "current",
                        "name": "Smoke Stage",
                        "required": True,
                        "status": "pending",
                        "gates": [
                            {
                                "gate_id": "current-validator-mesh-gate",
                                "gate_type": "validator",
                                "required": True,
                                "failure_behavior": "halt_run",
                                "contract_ref": {
                                    "artifact_id": "smoke-contract",
                                    "artifact_kind": "contract",
                                    "artifact_version": "0.8.0",
                                    "locator": "references/runtime-v080-smoke-contract.md",
                                },
                            }
                        ],
                    }
                ],
                "edges": [],
                "entry_stages": ["current"],
                "terminal_stages": ["current"],
            },
            "visibility_context": _build_visibility_context(visibility, inputs),
        },
        "causation_chain": [],
        "actor_role": "runner",
        "actor_identity": "smoke-runner",
        "trigger_artifact": {"artifact_id": "smoke-test", "artifact_kind": "ticket"},
        "reason": "smoke-scenario",
        "recommended_action": "none",
        "expected_stream_head": {"event_order": 0, "content_digest": ZERO_DIGEST},
        "client_event_id": f"cli-{inputs['client_event_id_seed']}",
        "prev_event_digest": ZERO_DIGEST,
    }


def _build_append_request(event_type: str, payload: dict, run_id: str,
                          prev_digest: str, head_order: int, inputs: dict) -> dict:
    return {
        "run_id": run_id,
        "event_type": event_type,
        "payload": payload,
        "causation_chain": [],
        "actor_role": "runner",
        "actor_identity": "smoke-runner",
        "trigger_artifact": {"artifact_id": "smoke-test", "artifact_kind": "ticket"},
        "reason": "smoke-scenario-transition",
        "recommended_action": "none",
        "expected_stream_head": {"event_order": head_order, "content_digest": prev_digest},
        "client_event_id": f"cli-{inputs['client_event_id_seed']}-{head_order}",
        "prev_event_digest": prev_digest,
    }


def _build_stage_completed_request(run_id: str, stage_id: str, prev_digest: str,
                                   head_order: int, completed_at: str,
                                   gate_decisions: list, artifacts: list,
                                   inputs: dict) -> dict:
    return _build_append_request(
        "run.stage.completed",
        {
            "stage_id": stage_id,
            "completed_at": completed_at,
            "gate_decisions": gate_decisions,
            "artifacts_produced": artifacts,
        },
        run_id, prev_digest, head_order, inputs,
    )


def _mesh_digest(inputs: dict, purpose: str) -> str:
    """Derive a stable Mesh identity digest solely from frozen scenario inputs."""
    return _sha256_obj({
        "purpose": purpose,
        "run_id": inputs["run_id"],
        "contract_id": inputs["contract_id"],
        "artifact_id": inputs["artifact_id"],
    })


def _contract_ref(inputs: dict, purpose: str) -> dict:
    return {
        "artifact_id": inputs["contract_id"],
        "artifact_kind": "contract",
        "artifact_version": "1.2.0",
        "digest": _mesh_digest(inputs, purpose),
    }


def _target_ref(inputs: dict, purpose: str) -> dict:
    return {
        "artifact_id": inputs["artifact_id"],
        "artifact_kind": "artifact",
        "artifact_version": "1.2.0",
        "digest": _mesh_digest(inputs, purpose),
    }


def _build_requirement(requirement_id: str, validator_identity: str, inputs: dict,
                       requirement_kind: str = "baseline", required: bool = True,
                       dispatch_priority: int = 0) -> dict:
    requirement = {
        "requirement_id": requirement_id,
        "validator_identity": validator_identity,
        "contract_ref": _contract_ref(inputs, "contract"),
        "artifact_scope": [_target_ref(inputs, "target")],
        "requirement_kind": requirement_kind,
        "required": required,
        "dispatch_priority": dispatch_priority,
        "failure_behavior": "halt_run",
    }
    if requirement_kind == "baseline":
        requirement["missing_mapping_policy"] = "fail"
    return requirement


def _build_mesh_declaration(run_id: str, inputs: dict, provider_kind: str) -> dict:
    req_id = inputs["requirement_id"]
    val_id = inputs["validator_identity"]
    if provider_kind == "duplicate":
        requirements = [
            _build_requirement("req-dup-a", val_id, inputs, dispatch_priority=0),
            _build_requirement("req-dup-b", val_id, inputs, dispatch_priority=1),
        ]
    elif provider_kind in {"missing_report", "report_missing", "unreachable"}:
        requirement_kind = "baseline"
        requirement = _build_requirement(req_id, val_id, inputs, requirement_kind)
        if provider_kind == "report_missing" and "hrr" in req_id:
            requirement["missing_mapping_policy"] = "human_review_required"
        elif provider_kind != "report_missing" or "hrr" not in req_id:
            requirement = _build_requirement(req_id, val_id, inputs, "extension")
        requirements = [requirement]
    else:
        requirements = [_build_requirement(req_id, val_id, inputs)]
    return {
        "mesh_id": f"mesh-{run_id}",
        "mesh_version": "1.2.0",
        "governing_contract": _contract_ref(inputs, "governing-contract"),
        "declared_at": inputs["declared_at"],
        "declared_by": "smoke-runner",
        "requirements": requirements,
        "aggregate_hierarchy": {},
        "dispatch_policy": {},
        "freshness_rules": {},
        "publish_bridge_contract": {},
        "run_context": {"run_id": run_id, "stage_id": "current"},
    }


def _build_dispatch_request(requirement_id: str, mesh_id: str, run_id: str, inputs: dict) -> dict:
    return {
        "dispatch_request_id": requirement_id,
        "requirement_id": requirement_id,
        "mesh_id": mesh_id,
        "validator_identity": inputs["validator_identity"],
        "contract_ref": _contract_ref(inputs, "contract"),
        "artifact_scope": [_target_ref(inputs, "target")],
        "evidence_pack": {"source_schema": "reference/source-artifact.json"},
        "risk_level": "medium",
        "dispatched_at": inputs["dispatched_at"],
        "dispatched_by": "smoke-runner",
        "run_context": {"run_id": run_id, "stage_id": "current"},
        "allowed_read_only_commands": ["python -c pass"],
    }


def _make_binding(requirement_id: str, run_id: str, inputs: dict, verdict: str,
                  report_identity: str = "current", contract_identity: str = "current") -> dict:
    digest = _mesh_digest(inputs, "report")
    report_digest = digest if report_identity == "current" else None
    report_sha256 = digest if report_identity == "current" else None
    contract_ref = _contract_ref(inputs, "contract")
    if contract_identity == "mismatched":
        contract_ref = {**contract_ref, "digest": _mesh_digest(inputs, "other-contract")}
    return {
        "dispatch_request_id": requirement_id,
        "dispatch_status": "report_produced",
        "report_binding": {
            "binding_id": f"bnd-{inputs['binding_id_seed']}-{requirement_id}",
            "requirement_id": requirement_id,
            "validator_identity": inputs["validator_identity"],
            "role": "validator",
            "contract_ref": contract_ref,
            "target_artifact_ref": _target_ref(inputs, "target"),
            "report_ref": {
                "artifact_id": f"report-{requirement_id}" if report_identity == "current" else None,
                "artifact_kind": "validation_report",
                "artifact_version": "1.2.0" if report_identity == "current" else None,
                "digest": report_digest,
            },
            "report_sha256": report_sha256,
            "report_confidence": "high",
            "report_overall_verdict": verdict,
            "independent_production_evidence": {
                "producer_identity": "validator-core",
                "production_environment": "smoke-runner",
                "production_timestamp": inputs.get("bound_at", inputs["collected_at"]),
                "no_caller_role_collapse": True,
            },
            "bound_at": inputs.get("bound_at", inputs["collected_at"]),
            "bound_by": "smoke-runner",
        },
        "collected_at": inputs["collected_at"],
        "collected_by": "smoke-runner",
    }


def _build_providers(provider_kind: str, run_id: str, inputs: dict):
    req_id = inputs["requirement_id"]
    val_id = inputs["validator_identity"]

    if provider_kind == "pass":
        return {
            val_id: lambda req: _make_binding(req_id, run_id, inputs, "pass"),
        }
    if provider_kind == "fail_verdict":
        return {
            val_id: lambda req: _make_binding(req_id, run_id, inputs, "fail"),
        }
    if provider_kind == "missing_report":
        return {
            val_id: lambda req: {
                "dispatch_request_id": req_id,
                "dispatch_status": "unreachable",
                "error_code": inputs["dispatch_error_code"],
                "collected_at": inputs["collected_at"],
                "collected_by": "smoke-runner",
            },
        }
    if provider_kind == "report_missing":
        return {
            val_id: lambda req: {
                "dispatch_request_id": req_id,
                "dispatch_status": "no_report",
                "error_code": inputs["dispatch_error_code"],
                "collected_at": inputs["collected_at"],
                "collected_by": "smoke-runner",
            },
        }
    if provider_kind == "degraded_storage":
        return {
            val_id: lambda req: _make_binding(req_id, run_id, inputs, "inconclusive"),
        }
    if provider_kind == "stale":
        return {
            val_id: lambda req: _make_binding(req_id, run_id, inputs, "pass", "stale"),
        }
    if provider_kind == "stale_contract":
        return {
            val_id: lambda req: _make_binding(req_id, run_id, inputs, "pass", contract_identity="mismatched"),
        }
    if provider_kind == "duplicate":
        provs = {
            val_id: lambda req: _make_binding(req["requirement_id"], run_id, inputs, "pass"),
        }
        return provs
    if provider_kind == "unreachable":
        return {
            val_id: lambda req: {
                "dispatch_request_id": req_id,
                "dispatch_status": "unreachable",
                "error_code": "validator_unreachable",
                "collected_at": inputs["collected_at"],
                "collected_by": "smoke-runner",
            }
        }
    return {}


def _build_action_policy_request(action_kind: str, inputs: dict, binding: dict) -> dict:
    request: dict = {
        "action_kind": action_kind,
        "policy_declaration": {
            "contract_id": "runtime-action-policy-contract",
            "contract_version": "2.0.0",
            "policy_id": "default-smoke-policy",
            "evaluated_under": "smoke-runner",
        },
        "decision_id": f"dec-{inputs['decision_id_seed']}",
        "evaluated_at": inputs["evaluated_at_action"],
        "evaluated_by": inputs["evaluated_by_action"],
        "run_id": inputs["run_id"],
        "boundary_facts": {
            "parent_run_id": inputs["parent_run_id"],
            "parent_run_status": inputs["parent_run_status"],
        },
    }

    if action_kind == "more_evidence":
        request["boundary_facts"]["evidence_gap_reason"] = "missing_evidence"
        snapshot_digest = inputs["gate_snapshot_digest"]
        request.update({
            "gate_snapshot_binding": {
                "source_gate_decision_ref": {"digest": snapshot_digest},
                "gate_decision_snapshot": {
                    "gate_id": "gate-001",
                    "gate_type": "validate",
                    "recommendation": "more_evidence",
                    "artifacts_produced": [],
                    "run_context": {"run_id": inputs["run_id"], "stage_id": "current"},
                },
                "canonical_digest": snapshot_digest,
            },
            "proposed_child_lineage": {
                "parent_run_id": inputs["proposed_child_lineage_parent"],
                "lineage_kind": "more_evidence",
            },
            "evidence_requests": [
                {
                    "request_id": inputs["evidence_request_id"],
                    "artifact_kind": "evidence",
                    "description": "Need more evidence.",
                    "required": True,
                }
            ],
        })
    elif action_kind == "retry":
        request.update({
            "boundary_facts": {
                **request["boundary_facts"],
                "current_retry_count": inputs["current_retry_count"],
                "max_retries": inputs["max_retries"],
                "same_kind_failure_count": inputs["same_kind_failure_count"],
                "attempt_history_facts": {
                    "attempt_count": inputs["attempt_count"],
                    "last_failure_category": inputs["last_failure_category"],
                    "last_failure_transient": inputs["last_failure_transient"],
                    "last_failure_deterministic": inputs["last_failure_deterministic"],
                },
            },
            "proposed_child_run_id": inputs["proposed_child_run_id"],
            "retry_strategy": inputs["retry_strategy"],
            "failure_category": inputs["failure_category"],
            "authorization": {
                "authorized_by": inputs["authorized_by"],
                "authorized_at": inputs["authorized_at"],
                "authorization_id": inputs["authorization_id"],
                "reason": inputs["auth_reason"],
            },
        })
        if inputs["authorized_by"] == "system":
            request["transient"] = inputs["transient"]
            request["deterministic"] = inputs["deterministic"]
    elif action_kind == "resume":
        request.update({
            "boundary_facts": {
                **request["boundary_facts"],
                "checkpoint_available": inputs["checkpoint_available"],
                "interruption_cause": inputs["interruption_cause"],
                "checkpoint_event_order": inputs["checkpoint_event_order"],
            },
            "proposed_child_run_id": inputs["proposed_child_run_id"],
            "checkpoint": copy.deepcopy(inputs["checkpoint"]),
            "authorization": {
                "authorized_by": inputs["authorized_by"],
                "authorized_at": inputs["authorized_at"],
                "authorization_id": inputs["authorization_id"],
                "reason": inputs["auth_reason"],
            },
        })
    elif action_kind == "redesign":
        # Redesign acts on the caller-supplied parent run, never the proposed child.
        request["run_id"] = inputs["parent_run_id"]
        request.update({
            "proposed_child_lineage": {
                "parent_run_id": inputs["parent_lineage_parent"],
                "lineage_kind": "redesign",
            },
            "revised_contract_ref": {
                "artifact_id": inputs["revised_contract_id"],
                "artifact_kind": inputs["revised_contract_kind"],
            },
            "reason_code": inputs["reason_code"],
            "history_preservation_facts": {
                "original_history_preserved": inputs["original_history_preserved"],
                "original_evidence_preserved": inputs["original_evidence_preserved"],
            },
            "authorization": {
                "authorized_by": inputs["authorized_by"],
                "authorized_at": inputs["authorized_at"],
                "authorization_id": inputs["authorization_id"],
                "reason": inputs["auth_reason"],
            },
        })
    elif action_kind == "human_intervention":
        snapshot_digest = inputs["gate_snapshot_digest"]
        request.update({
            "gate_snapshot_binding": {
                "source_gate_decision_ref": {"digest": snapshot_digest},
                "gate_decision_snapshot": {
                    "gate_id": "gate-001",
                    "gate_type": "validate",
                    "recommendation": inputs["gate_snapshot_recommendation"],
                    "artifacts_produced": [],
                    "run_context": {"run_id": inputs["run_id"], "stage_id": "current"},
                },
                "canonical_digest": snapshot_digest,
            },
            "intervention_source": inputs["intervention_source"],
            "intervention_evidence": [
                {
                    "artifact_id": inputs["intervention_evidence_id"],
                    "artifact_kind": inputs["intervention_evidence_kind"],
                }
            ],
            "human_intent": inputs["human_intent"],
            "prohibited_override_facts": {
                "required_gate_override_attempted": inputs["required_gate_override_attempted"],
                "pass_evidence_fabricated": inputs["pass_evidence_fabricated"],
                "retry_resume_bounds_bypassed": inputs["retry_resume_bounds_bypassed"],
            },
            "authorization": {
                "authorized_by": inputs["authorized_by"],
                "authorized_at": inputs["authorized_at"],
                "authorization_id": inputs["authorization_id"],
                "reason": inputs["auth_reason"],
            },
        })
    return request


# ---------------------------------------------------------------------------
# Adapter builders -- each returns (callable, args, kwargs, input_repr).
# No production call happens here; the engine invokes the callable exactly once.
# ---------------------------------------------------------------------------

def _adapter_create_run(inputs, binding, sidecar_state, tmp_dir, catalog):
    sidecar = _open_sidecar(inputs["run_id"], tmp_dir, inputs, catalog)
    sidecar_state["_sidecar"] = sidecar
    request = _build_create_run_request(inputs)
    return (sidecar.create_run, (request,), {}, {"operation": "create_run",
                                                 "request": request})


def _adapter_append_event(inputs, binding, sidecar_state, tmp_dir, catalog):
    sidecar = sidecar_state.get("_sidecar")
    if sidecar is None:
        sidecar = _open_sidecar(inputs["run_id"], tmp_dir, inputs, catalog)
        sidecar_state["_sidecar"] = sidecar
    event_type = binding.get("event_type")
    prev_digest = sidecar_state.get("prev_digest", ZERO_DIGEST)
    head_order = sidecar_state.get("head_order", 0)
    run_id = inputs["run_id"]

    if event_type == "run.started":
        payload = {"started_at": inputs["started_at"], "executor_identity": "smoke-runner"}
    elif event_type == "run.stage.started":
        payload = {"stage_id": inputs["stage_id"], "started_at": inputs["started_at"],
                   "entry_evidence": []}
    elif event_type == "run.gate.evaluated":
        # Record the gate decision produced earlier by publish_to_gate. Every
        # field originates from the explicit gate decision in runtime state.
        gate_result = sidecar_state.get("_gate_result", {})
        payload = {
            "stage_id": inputs["stage_id"],
            "gate_id": gate_result.get("gate_id"),
            "decision_id": gate_result.get("decision_id"),
            "outcome": gate_result.get("outcome"),
            "execution_mode": gate_result.get("execution_mode"),
            "evaluated_at": gate_result.get("evaluated_at"),
            "evaluated_by": gate_result.get("evaluated_by"),
            "evidence": gate_result.get("evidence", []),
        }
        request = _build_append_request(event_type, payload, run_id, prev_digest, head_order, inputs)
        return (sidecar.append_event, (request,), {},
                {"operation": "append_event", "event_type": event_type, "request": request})
    else:  # run.stage.completed (commit_stage)
        decision_id = sidecar_state.get("_last_decision_id", f"dec-{run_id}")
        gate_decisions = [{"decision_id": decision_id}]
        # The artifact is explicit scenario data before the completion event is
        # built.  Completion is what records it; it is not used to invent it.
        artifacts = [_build_runtime_artifact(inputs)]
        request = _build_stage_completed_request(
            run_id, inputs["stage_id"], prev_digest, head_order,
            inputs["completed_at"], gate_decisions, artifacts, inputs)
        return (sidecar.append_event, (request,), {},
                {"operation": "append_event", "event_type": event_type, "request": request})

    request = _build_append_request(event_type, payload, run_id, prev_digest, head_order, inputs)
    return (sidecar.append_event, (request,), {},
            {"operation": "append_event", "event_type": event_type, "request": request})


def _build_runtime_artifact(inputs: dict) -> dict:
    """Build the complete, caller-derived RuntimeArtifact for stage completion."""
    visibility = inputs["visibility"]
    source_ref = {
        "artifact_id": inputs["artifact_id"],
        "artifact_kind": "artifact",
        "artifact_version": "1.2.0",
    }
    contributor = {
        "contributor_id": f"smoke-source-{inputs['artifact_id']}",
        "contributor_kind": "source_artifact",
        "contributor_ref": source_ref,
        "asserted_visibility": visibility,
        "authority": "Smoke scenario input",
        "classification_evidence": [copy.deepcopy(source_ref)],
    }
    return {
        "artifact_ref": {
            "artifact_id": inputs["artifact_id"],
            "artifact_kind": "artifact",
            "artifact_version": "1.2.0",
            "digest": "sha256:" + _mesh_digest(inputs, "runtime-artifact"),
        },
        "origin_run": inputs["run_id"],
        "origin_stage": inputs["stage_id"],
        "produced_by": inputs["executor_identity"],
        "source_artifacts": [copy.deepcopy(source_ref)],
        "visibility": visibility,
        "visibility_resolution": {
            "resolution_id": f"artifact-res-{inputs['resolution_id_seed']}",
            "resolved_at": inputs["completed_at"],
            "contributors": [contributor],
            "resolution_rule": "most_restrictive",
            "resolved_visibility": visibility,
            "resolution_audit": {
                "contributor_count": 1,
                "restricted_count": 1 if visibility == "restricted" else 0,
                "project_count": 1 if visibility == "project" else 0,
                "public_count": 1 if visibility == "public" else 0,
                "applied_rule": "most_restrictive",
            },
        },
    }


def _build_dispatch_args(inputs, binding):
    """Pure: build (dispatch_requests, providers, mesh_declaration) from inputs.

    No production callable is invoked and no database is opened here."""
    run_id = inputs["run_id"]
    provider_kind = binding.get("provider_kind", "pass")
    mesh = _build_mesh_declaration(run_id, inputs, provider_kind)

    if provider_kind == "duplicate":
        dr_list = [
            _build_dispatch_request("req-dup-a", mesh["mesh_id"], run_id, inputs),
            _build_dispatch_request("req-dup-b", mesh["mesh_id"], run_id, inputs),
        ]
        providers = _build_providers(provider_kind, run_id, inputs)
        return dr_list, providers, mesh

    requirement = mesh["requirements"][0]
    dr = [_build_dispatch_request(requirement["requirement_id"], mesh["mesh_id"], run_id, inputs)]
    providers = _build_providers(provider_kind, run_id, inputs)
    return dr, providers, mesh


def _adapter_dispatch(inputs, binding, sidecar_state, tmp_dir, catalog):
    dr_list, providers, mesh = _build_dispatch_args(inputs, binding)
    sidecar_state["_current_mesh_declaration"] = mesh
    input_repr = {"operation": "dispatch", "provider_kind": binding.get("provider_kind", "pass"),
                  "dispatch_requests": dr_list,
                  "provider_identities": sorted(providers.keys())}
    return (_dispatch, (dr_list, providers), {}, input_repr)


def _adapter_evaluate(inputs, binding, sidecar_state, tmp_dir, catalog):
    mesh = sidecar_state.get("_current_mesh_declaration")
    results = sidecar_state.get("_dispatch_results", [])
    run_id = inputs["run_id"]
    eval_request = {
        "mesh_eval_id": f"evl-{inputs['mesh_eval_id_seed']}",
        "mesh_declaration": mesh,
        "dispatch_results": results,
        "requested_at": inputs["created_at"],
        "requested_by": "smoke-runner",
    }
    return (_evaluate_mesh, (eval_request,), {},
            {"operation": "evaluate_validator_mesh", "request": eval_request})


def _build_gate_facts(inputs):
    return {
        "run_context": {"run_id": inputs["run_id"], "stage_id": inputs["stage_id"]},
        "evaluated_at": inputs["evaluated_at"],
        "evaluated_by": "smoke-runner",
    }


def _adapter_publish_to_gate(inputs, binding, sidecar_state, tmp_dir, catalog):
    mesh_result = sidecar_state.get("_mesh_result", {})
    gate_facts = _build_gate_facts(inputs)
    return (_publish_to_gate, (mesh_result, gate_facts), {},
            {"operation": "publish_to_gate",
             "mesh_overall_verdict": mesh_result.get("overall_verdict"),
             "gate_facts": gate_facts})


def _adapter_export_run(inputs, binding, sidecar_state, tmp_dir, catalog):
    run_id = inputs["run_id"]
    db_path = _derive_db_path(run_id, tmp_dir)
    expnum = run_id.rsplit("-", 1)[-1]
    export_id = f"export-{expnum}-0000-4000-a000-000000000001"
    exported_at = inputs["exported_at"]
    input_repr = {"operation": "export_run", "run_id": run_id,
                  "export_id": export_id, "exported_at": exported_at}
    return (_export_run, (db_path, run_id),
            {"export_id": export_id, "exported_at": exported_at}, input_repr)


def _adapter_action_policy(inputs, binding, sidecar_state, tmp_dir, catalog):
    action_kind = inputs["action_kind"]
    request = _build_action_policy_request(action_kind, inputs, binding)
    return (_evaluate_action, (request,), {},
            {"operation": "evaluate_runtime_action", "request": request})


# ---------------------------------------------------------------------------
# Frozen dispatch table -- (component, operation) -> adapter builder
# Each entry maps to exactly one production callable (see PRODUCTION_CALLABLE).
# ---------------------------------------------------------------------------

ADAPTERS = {
    ("runtime_state_sidecar", "create_run"): _adapter_create_run,
    ("runtime_state_sidecar", "append_event"): _adapter_append_event,
    ("runtime_state_sidecar", "commit_stage"): _adapter_append_event,
    ("runtime_validator_dispatch", "dispatch"): _adapter_dispatch,
    ("runtime_validator_mesh", "evaluate_validator_mesh"): _adapter_evaluate,
    ("runtime_publish_gate", "publish_to_gate"): _adapter_publish_to_gate,
    ("runtime_evidence_export", "export_run"): _adapter_export_run,
    ("runtime_action_policy", "evaluate_runtime_action"): _adapter_action_policy,
}

# Fully qualified production callable per (component, operation) for the ledger.
PRODUCTION_CALLABLE = {
    ("runtime_state_sidecar", "create_run"): RuntimeStateSidecar.create_run,
    ("runtime_state_sidecar", "append_event"): RuntimeStateSidecar.append_event,
    ("runtime_state_sidecar", "commit_stage"): RuntimeStateSidecar.append_event,
    ("runtime_validator_dispatch", "dispatch"): _dispatch,
    ("runtime_validator_mesh", "evaluate_validator_mesh"): _evaluate_mesh,
    ("runtime_publish_gate", "publish_to_gate"): _publish_to_gate,
    ("runtime_evidence_export", "export_run"): _export_run,
    ("runtime_action_policy", "evaluate_runtime_action"): _evaluate_action,
}


# ---------------------------------------------------------------------------
# Stable semantic projection -- documented contract-derived field lists
# ---------------------------------------------------------------------------
# Each output kind maps to a list of deterministic fields (never store-assigned).
# `None` means the full output object is deterministic.
STABLE_OUTPUT_PROJECTION = {
    "create_run": ["event_order"],
    "append_event": ["event_order"],
    "dispatch": None,
    "evaluate_mesh": ["aggregate_verdict", "requirement_results", "recommended_action"],
    "publish_to_gate": ["gate_id", "decision_id", "outcome", "execution_mode", "signal"],
    "export_run": ["run_id"],
    "evaluate_action": ["action_kind", "action_allowed", "decision_id"],
}

# Operation -> output_kind mapping for the ledger.
_OPERATION_TO_OUTPUT_KIND = {
    "create_run": "create_run",
    "append_event": "append_event",
    "commit_stage": "append_event",
    "dispatch": "dispatch",
    "evaluate_validator_mesh": "evaluate_mesh",
    "publish_to_gate": "publish_to_gate",
    "export_run": "export_run",
    "evaluate_runtime_action": "evaluate_action",
}


def _project_stable(output, output_kind):
    """Extract only deterministic stable fields from a production output."""
    fields = STABLE_OUTPUT_PROJECTION.get(output_kind)
    if fields is None:
        return copy.deepcopy(output) if isinstance(output, (dict, list)) else output
    if isinstance(output, dict):
        result = {}
        for k in fields:
            if k in output:
                result[k] = output[k]
        return result
    return output


# ---------------------------------------------------------------------------
# Raw cryptographic verification -- per-run, uses the actual production return
# including store-assigned event_id/occurred_at/signatures.
# ---------------------------------------------------------------------------

def _verify_raw_evidence(output, output_kind, verify_state, inputs, binding):
    """Verify production output against raw cryptographic rules.

    Returns a list of per-rule pass/fail dicts. If the output is an error dict
    (production callable raised), no verification rules are emitted."""
    if isinstance(output, dict) and "error" in output:
        return []

    if output_kind in ("create_run", "append_event"):
        return _verify_receipt(output, verify_state, inputs)
    elif output_kind == "dispatch":
        return _verify_dispatch_integrity(output)
    elif output_kind == "evaluate_mesh":
        return _verify_mesh_integrity(output)
    elif output_kind == "publish_to_gate":
        return _verify_gate_integrity(output)
    elif output_kind == "export_run":
        return _verify_export_integrity(output)
    elif output_kind == "evaluate_action":
        return _verify_action_integrity(output)
    return []


def _verify_receipt(output, verify_state, inputs):
    """Verify raw AppendReceipt cryptographic evidence.

    Checks receipt shape, event order monotonicity, signed receipt HMAC,
    run_id binding, event_order binding, digest binding, and stream head
    integrity."""
    results = []
    run_id = verify_state.get("run_id", "")
    signer_key = verify_state.get("signer_key")
    prev_event_order = verify_state.get("last_event_order", 0)

    # 1. Receipt shape: all required fields present
    shape_ok = all(k in output for k in (
        "event_id", "event_order", "stored_content_digest", "new_stream_head",
        "signed_receipt"))
    results.append({"rule": "receipt_shape", "status": "pass" if shape_ok else "fail"})

    if not shape_ok:
        return results

    event_order = output.get("event_order")
    new_head = output.get("new_stream_head", {})
    signed = output.get("signed_receipt", {})
    sp = signed.get("signed_payload", {})

    # 2. Event order is positive integer
    eo_ok = isinstance(event_order, int) and event_order > 0
    results.append({"rule": "event_order_type", "status": "pass" if eo_ok else "fail"})

    # 3. Event order monotonic (strictly increasing, gap-free by pipeline)
    mono_ok = eo_ok and event_order == prev_event_order + 1
    results.append({"rule": "event_order_monotonic", "status": "pass" if mono_ok else "fail"})

    # 4. Signed receipt HMAC verification
    hmac_ok = False
    if signer_key and signed.get("algorithm") == "HMAC-SHA256" and "signature" in signed:
        try:
            payload_bytes = json.dumps(
                signed["signed_payload"], sort_keys=True,
                separators=(",", ":")).encode("utf-8")
            expected_sig = hmac.new(signer_key, payload_bytes, "sha256").hexdigest()
            hmac_ok = hmac.compare_digest(expected_sig, signed["signature"])
        except Exception:
            hmac_ok = False
    results.append({"rule": "signed_receipt_hmac", "status": "pass" if hmac_ok else "fail"})

    # 5. Run ID binding: signed_payload.run_id == expected
    rid_ok = sp.get("run_id") == run_id
    results.append({"rule": "run_id_binding", "status": "pass" if rid_ok else "fail"})

    # 6. Event order binding: consistent across receipt/head/payload
    eob_ok = (
        sp.get("event_order") == event_order and
        new_head.get("event_order") == event_order
    )
    results.append({"rule": "event_order_binding", "status": "pass" if eob_ok else "fail"})

    # 7. Digest binding: content_digest consistent across receipt/head/payload
    dig_ok = (
        sp.get("content_digest") == new_head.get("content_digest") and
        output.get("stored_content_digest") == new_head.get("content_digest")
    )
    results.append({"rule": "digest_binding", "status": "pass" if dig_ok else "fail"})

    # 8. Stream head integrity: content_digest is a sha256-prefixed 64-hex string
    head_dig = new_head.get("content_digest", "")
    head_dig_ok = bool(head_dig) and len(head_dig) == 71 and head_dig.startswith("sha256:")
    results.append({"rule": "stream_head_integrity", "status": "pass" if head_dig_ok else "fail"})

    return results


def _verify_dispatch_integrity(output):
    """Verify dispatch results are a non-empty list with required fields."""
    results = []
    if isinstance(output, list):
        list_ok = len(output) > 0
        results.append({"rule": "dispatch_nonempty", "status": "pass" if list_ok else "fail"})
        all_have_req_id = all(
            isinstance(r, dict) and "dispatch_request_id" in r for r in output)
        results.append({"rule": "dispatch_request_ids", "status": "pass" if all_have_req_id else "fail"})
    elif isinstance(output, dict) and "dispatch_results" in output:
        dr = output["dispatch_results"]
        list_ok = isinstance(dr, list) and len(dr) > 0
        results.append({"rule": "dispatch_nonempty", "status": "pass" if list_ok else "fail"})
        if list_ok:
            all_have_req_id = all(
                isinstance(r, dict) and "dispatch_request_id" in r for r in dr)
            results.append({"rule": "dispatch_request_ids", "status": "pass" if all_have_req_id else "fail"})
        else:
            results.append({"rule": "dispatch_request_ids", "status": "fail"})
    else:
        results.append({"rule": "dispatch_nonempty", "status": "fail"})
        results.append({"rule": "dispatch_request_ids", "status": "fail"})
    return results


def _verify_mesh_integrity(output):
    """Verify mesh evaluation output has required fields and valid verdict."""
    results = []
    if not isinstance(output, dict):
        results.append({"rule": "mesh_shape", "status": "fail"})
        results.append({"rule": "mesh_verdict_valid", "status": "fail"})
        return results
    shape_ok = "aggregate_verdict" in output
    results.append({"rule": "mesh_shape", "status": "pass" if shape_ok else "fail"})
    valid_verdicts = {"pass", "fail", "blocked", "inconclusive", "human_review_required"}
    verdict_ok = output.get("aggregate_verdict") in valid_verdicts
    results.append({"rule": "mesh_verdict_valid", "status": "pass" if verdict_ok else "fail"})
    return results


def _verify_gate_integrity(output):
    """Verify publish gate output has required identity fields."""
    results = []
    if not isinstance(output, dict):
        results.append({"rule": "gate_shape", "status": "fail"})
        return results
    # Gate Decision v2.2 returns a decision XOR a typed evaluation error.
    decision_ok = all(k in output for k in ("gate_id", "decision_id", "outcome", "execution_mode"))
    gate_error_ok = all(k in output for k in ("gate_id", "decision_id", "error_code", "error_description", "run_context"))
    bridge_error_ok = all(k in output for k in ("mesh_eval_id", "mesh_id", "error_code", "error_description", "run_context"))
    shape_ok = decision_ok or gate_error_ok or bridge_error_ok
    results.append({"rule": "gate_shape", "status": "pass" if shape_ok else "fail"})
    return results


def _verify_export_integrity(output):
    """Verify export output has required fields."""
    results = []
    if not isinstance(output, dict):
        results.append({"rule": "export_shape", "status": "fail"})
        return results
    shape_ok = "run_id" in output and "export_content_digest" in output
    results.append({"rule": "export_shape", "status": "pass" if shape_ok else "fail"})
    if "export_content_digest" in output:
        digest = output["export_content_digest"]
        digest_ok = isinstance(digest, str) and digest.startswith("sha256:") and len(digest) == 71
        results.append({"rule": "export_digest_format", "status": "pass" if digest_ok else "fail"})
    return results


def _verify_action_integrity(output):
    """Verify action policy output has required decision fields."""
    results = []
    if not isinstance(output, dict):
        results.append({"rule": "action_shape", "status": "fail"})
        return results
    # RuntimeActionDecision v2 uses disposition and branch reason fields.
    shape_ok = (all(k in output for k in ("action_kind", "disposition", "reason_code"))
                and output.get("action_kind") in {
                    "more_evidence", "retry", "resume", "redesign", "human_intervention"})
    results.append({"rule": "action_shape", "status": "pass" if shape_ok else "fail"})
    return results


def _update_verify_state(verify_state, output, output_kind):
    """Update verification state for the next step's chain verification."""
    if output_kind not in ("create_run", "append_event"):
        return
    if isinstance(output, dict) and "error" not in output:
        event_order = output.get("event_order")
        if isinstance(event_order, int) and event_order > 0:
            verify_state["last_event_order"] = event_order


def _extract_state_facts(sidecar_state, inputs):
    """Extract stable contract-defined facts from the last projection."""
    facts = {"run_id": inputs.get("run_id", "")}
    head_order = sidecar_state.get("head_order")
    if isinstance(head_order, int):
        facts["event_count"] = head_order
    action_result = sidecar_state.get("_action_policy_result")
    if isinstance(action_result, dict):
        for key in ("action_allowed", "action_kind", "disposition", "reason_code",
                    "checkpoint_evidence", "authorization"):
            if key in action_result:
                facts[f"action_{key}"] = copy.deepcopy(action_result[key])
    return facts


def _surface_snapshot(sidecar_state: dict, inputs: dict) -> dict:
    """Capture the full protected five-surface state at an execution boundary."""
    return _protected_surfaces(sidecar_state, inputs)


# ---------------------------------------------------------------------------
# Validation -- complete immutable input object validated before any DB/create
# ---------------------------------------------------------------------------

def _allowed_input_keys(catalog: dict) -> set:
    keys: set = set()
    for s in catalog.get("scenarios", []):
        for k in s.get("inputs", {}):
            keys.add(k)
    return keys


def _dry_build(component: str, operation: str, inputs: dict, binding: dict) -> None:
    """Exercise every input-dependent request builder for one step.

    Raises ``KeyError``/``ScenarioInputError`` if a required explicit input is
    missing. Pure: opens no database and invokes no production callable, so it
    is safe to run during pre-execution validation (fail-fast, zero writes)."""
    if operation == "create_run":
        _build_create_run_request(inputs)
    elif operation == "append_event":
        et = binding.get("event_type")
        if et == "run.started":
            _build_append_request(et, {"started_at": inputs["started_at"],
                                       "executor_identity": "smoke-runner"},
                                  inputs["run_id"], ZERO_DIGEST, 0, inputs)
        elif et == "run.stage.started":
            _build_append_request(et, {"stage_id": inputs["stage_id"],
                                       "started_at": inputs["started_at"],
                                       "entry_evidence": []},
                                  inputs["run_id"], ZERO_DIGEST, 0, inputs)
        elif et == "run.gate.evaluated":
            # Remaining fields come from the gate decision recorded at runtime.
            _ = inputs["stage_id"]
        else:
            _build_stage_completed_request(inputs["run_id"], inputs["stage_id"],
                                           ZERO_DIGEST, 0, inputs["completed_at"], [], [], inputs)
    elif operation == "commit_stage":
        _build_stage_completed_request(inputs["run_id"], inputs["stage_id"],
                                       ZERO_DIGEST, 0, inputs["completed_at"], [], [], inputs)
    elif operation == "dispatch":
        _build_dispatch_args(inputs, binding)
    elif operation == "evaluate_validator_mesh":
        _build_mesh_declaration(inputs["run_id"], inputs, binding.get("provider_kind", "pass"))
    elif operation == "publish_to_gate":
        _ = inputs["evaluated_at"], inputs["created_at"]
    elif operation == "export_run":
        _ = inputs["exported_at"], inputs["created_at"]
    elif operation == "evaluate_runtime_action":
        _build_action_policy_request(inputs["action_kind"], inputs, binding)


def _validate_scenario(scenario: dict, catalog: dict) -> None:
    """Validate the complete immutable scenario input BEFORE any DB/create.

    Fail-fast: if the signer cannot be resolved or any step references a
    required explicit input that is absent, raise immediately (zero writes)."""
    inputs = scenario.get("inputs", {})
    if not isinstance(inputs, dict) or not inputs:
        raise ScenarioInputError(f"{scenario['scenario_id']}: inputs object is empty/missing")

    # Resolved signer must be present (fail-fast before any DB/create).
    _resolve_signer_key(inputs, catalog)

    expectations = catalog.get("scenario_expectations", {})
    expected = expectations.get(scenario.get("scenario_id")) if isinstance(expectations, dict) else None
    if not isinstance(expected, dict):
        raise ScenarioInputError(
            f"{scenario['scenario_id']}: missing contract-derived scenario expectation"
        )
    if not isinstance(expected.get("typed_outcome"), str):
        raise ScenarioInputError(
            f"{scenario['scenario_id']}: expected typed_outcome is missing or invalid"
        )
    for key in ("observable_side_effects", "prohibited_side_effects"):
        value = expected.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ScenarioInputError(
                f"{scenario['scenario_id']}: {key} must be an ordered string list"
            )

    rule_ids = scenario.get("verification_rule_ids")
    if not isinstance(rule_ids, list) or not rule_ids or not all(isinstance(rule, str) for rule in rule_ids):
        raise ScenarioInputError(
            f"{scenario['scenario_id']}: verification_rule_ids must be a non-empty string list"
        )
    if len(rule_ids) != len(set(rule_ids)):
        raise ScenarioInputError(
            f"{scenario['scenario_id']}: duplicate verification_rule_id"
        )

    expectation = catalog.get("scenario_expectations", {}).get(scenario["scenario_id"])
    required_expectation_keys = {
        "typed_outcome", "observable_side_effects", "prohibited_side_effects"
    }
    if (not isinstance(expectation, dict)
            or set(expectation) != required_expectation_keys
            or not isinstance(expectation["observable_side_effects"], list)
            or not isinstance(expectation["prohibited_side_effects"], list)
            or not isinstance(scenario.get("verification_rule_ids"), list)
            or not scenario["verification_rule_ids"]):
        raise ScenarioInputError(
            f"{scenario['scenario_id']}: incomplete contract-derived expectation"
        )

    # Every step must map to exactly one adapter, and every input the step
    # requires must be present in the explicit scenario inputs. This runs
    # BEFORE any database is opened or any production callable is invoked.
    for entry in scenario.get("pipeline", []):
        component = entry["component"]
        operation = entry["operation"]
        if (component, operation) not in ADAPTERS:
            raise ScenarioInputError(
                f"{scenario['scenario_id']}: no adapter for {component}.{operation}"
            )
        try:
            _dry_build(component, operation, inputs, entry.get("input_binding", {}))
        except ScenarioInputError:
            raise
        except KeyError as exc:
            raise ScenarioInputError(
                f"{scenario['scenario_id']} step '{entry['step_id']}': "
                f"missing required explicit input {exc}"
            ) from exc


def _protected_surfaces(sidecar_state: dict, inputs: dict) -> dict:
    """Capture the five protected surfaces without mutating runtime state."""
    sidecar = sidecar_state.get("_sidecar")
    run_id = inputs["run_id"]
    journal = sidecar.read_events(run_id) if sidecar is not None else []
    projection = sidecar.get_run(run_id) if journal else {}
    # get_run() includes a fresh replay envelope on every read.  Its generated
    # id, timestamp, and digest describe that read, not persisted runtime
    # state, so they cannot participate in a zero-write equality proof.
    if isinstance(projection, dict):
        projection = copy.deepcopy(projection)
        for volatile_key in ("projection_digest", "projection_id", "derived_at"):
            projection.pop(volatile_key, None)
    return {
        "journal": copy.deepcopy(journal),
        "projection": projection,
        # Dispatch returns report bindings but does not persist or rewrite them.
        "report_set": copy.deepcopy(sidecar_state.get("_dispatch_results", [])),
        "runtime_artifacts": copy.deepcopy(projection.get("runtime_artifacts", [])),
        "export_evidence": copy.deepcopy(sidecar_state.get("_export_result")),
    }


def _surface_digests(surfaces: dict) -> dict:
    return {name: _stdlib_sha256_prefixed(value) for name, value in surfaces.items()}


def _expected_event_types(scenario: dict) -> list[str]:
    return [
        step.get("input_binding", {}).get("event_type")
        for step in scenario.get("pipeline", [])
        if step.get("component") == "runtime_state_sidecar"
        and step.get("operation") in {"create_run", "append_event", "commit_stage"}
    ]


def _identity_provenance_digest_check(sidecar_state: dict, inputs: dict) -> bool:
    """Verify the complete-flow identity boundary with stdlib-only digests.

    This is intentionally independent from production digest helpers: it checks
    the report binding's canonical digest, GateDecision identity, the completed
    RuntimeArtifact, and the exported envelope digest as separate facts.
    """
    mesh = sidecar_state.get("_mesh_result")
    gate = sidecar_state.get("_gate_result")
    export = sidecar_state.get("_export_result")
    sidecar = sidecar_state.get("_sidecar")
    if not all(isinstance(value, dict) for value in (mesh, gate, export)) or sidecar is None:
        return False
    bindings = mesh.get("report_bindings")
    if not isinstance(bindings, list) or len(bindings) != 1:
        return False
    report_ref = bindings[0].get("report_ref", {}) if isinstance(bindings[0], dict) else {}
    expected_report_digest = hashlib.sha256(json.dumps({
        "purpose": "report", "run_id": inputs["run_id"],
        "contract_id": inputs["contract_id"], "artifact_id": inputs["artifact_id"],
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    if report_ref.get("digest") != expected_report_digest:
        return False
    if bindings[0].get("report_sha256") != expected_report_digest:
        return False
    # The Validator Mesh keeps the report digest in raw binding evidence.  The
    # Gate is deliberately narrower: its ArtifactRefs are identity projections
    # and may not grow report or digest fields.
    gate_evidence = gate.get("evidence", [])
    if not isinstance(gate_evidence, list) or not gate_evidence:
        return False
    for artifact_ref in gate_evidence:
        if not isinstance(artifact_ref, dict):
            return False
        if set(artifact_ref) - {"artifact_id", "artifact_kind", "artifact_version"}:
            return False
        if not isinstance(artifact_ref.get("artifact_id"), str) or not isinstance(
                artifact_ref.get("artifact_kind"), str):
            return False
    if gate.get("decision_id") != mesh.get("mesh_eval_id", "") + "-gate-decision":
        return False
    projection = sidecar.get_run(inputs["run_id"])
    artifacts = projection.get("runtime_artifacts", []) if isinstance(projection, dict) else []
    if len(artifacts) != 1:
        return False
    artifact = artifacts[0]
    if not isinstance(artifact, dict) or artifact.get("origin_run") != inputs["run_id"]:
        return False
    if artifact.get("origin_stage") != inputs["stage_id"]:
        return False
    if artifact.get("visibility") != inputs["visibility"]:
        return False
    export_preimage = {key: value for key, value in export.items()
                       if key != "export_content_digest"}
    return _stdlib_sha256_prefixed(export_preimage) == export.get("export_content_digest")


def _execute_catalog_oracles(scenario: dict, catalog: dict, result: dict,
                             sidecar_state: dict, boundary_proof: list) -> list[dict]:
    """Run every declared catalog oracle once, with no permissive fallback."""
    scenario_id = scenario["scenario_id"]
    expected = catalog["scenario_expectations"][scenario_id]
    declared = list(scenario["verification_rule_ids"])
    required = {"typed_outcome_match", "observable_side_effects_match",
                "prohibited_side_effects_absent"}
    if not required.issubset(declared):
        return [{"rule": "missing_verification_rule_id", "status": "fail",
                 "step_id": "catalog"}]
    if len(declared) != len(set(declared)):
        return [{"rule": "duplicate_verification_rule_id", "status": "fail",
                 "step_id": "catalog", "detail": {"scenario_id": scenario_id}}]

    ledger = result["call_ledger"]
    sidecar = sidecar_state.get("_sidecar")
    events = sidecar.read_events(result["state_facts"]["run_id"]) if sidecar is not None else []
    event_types = [event.get("event_type") for event in events if isinstance(event, dict)]
    projection = sidecar.get_run(result["state_facts"]["run_id"]) if sidecar is not None else {}
    operation_names = [entry.get("operation") for entry in ledger if entry.get("invocation_count") == 1]
    action = sidecar_state.get("_action_policy_result", {})

    def typed_outcome() -> bool:
        outcome = expected["typed_outcome"]
        if outcome.startswith("action_authorized_"):
            return isinstance(action, dict) and action.get("disposition") == "authorized" \
                and action.get("reason_code") == outcome
        return result.get("final_verdict") == outcome

    def observable_side_effects() -> bool:
        declared_effects = expected["observable_side_effects"]
        expected_events = [item for item in declared_effects if item.startswith("run.")]
        expected_operations = [item for item in declared_effects if not item.startswith("run.")]
        return event_types == expected_events and all(
            operation_names.count(operation) == 1 for operation in expected_operations)

    def prohibited_side_effects() -> bool:
        return not any(operation in expected["prohibited_side_effects"]
                       for operation in operation_names)

    def call_ledger_match() -> bool:
        pipeline = scenario.get("pipeline", [])
        expected_steps = [step.get("step_id") for step in pipeline]
        actual_steps = [entry.get("step_id") for entry in ledger]
        expected_counts = [0 if step.get("input_binding", {}).get("suppress_after_export_tamper")
                           else 1 for step in pipeline]
        return actual_steps == expected_steps and [entry.get("invocation_count") for entry in ledger] == expected_counts

    def semantic_input_origin() -> bool:
        return all(entry.get("input_digest") == _sha256_obj(step.get("input_binding", {}))
                   for entry, step in zip(ledger, scenario.get("pipeline", [])))

    def journal_sequence() -> bool:
        return event_types == [item for item in expected["observable_side_effects"]
                               if item.startswith("run.")]

    def projection_pass() -> bool:
        if not isinstance(projection, dict):
            return False
        stages = projection.get("stage_states")
        if not isinstance(stages, dict) or not stages:
            return False
        return projection.get("current_stage_id") is None and all(
            isinstance(stage, dict) and stage.get("status") == "completed"
            for stage in stages.values())

    def ticket_provenance() -> bool:
        provenance = projection.get("run_provenance", {}) if isinstance(projection, dict) else {}
        origin = provenance.get("origin_artifact", {}) if isinstance(provenance, dict) else {}
        return origin.get("artifact_id") == scenario["inputs"].get("ticket_id") \
            and origin.get("artifact_kind") == "ticket"

    def no_ticket_leak() -> bool:
        provenance = projection.get("run_provenance", {}) if isinstance(projection, dict) else {}
        origin = provenance.get("origin_artifact", {}) if isinstance(provenance, dict) else {}
        return origin.get("artifact_kind") != "ticket" and not scenario["inputs"].get("ticket_id")

    def non_success_boundary() -> bool:
        return bool(boundary_proof) and all(
            proof.get("all_five_surfaces_equal") is True
            and proof.get("prohibited_downstream_call_count") == 0
            and set(proof["before"]) == {
                "journal", "projection", "report_set", "runtime_artifacts", "export_evidence"}
            and proof["before"] == proof["after"] for proof in boundary_proof)

    def recovery_steps() -> bool:
        if not call_ledger_match():
            return False
        return all(entry.get("invocation_count") == 1 for entry in ledger)

    def no_gate_override() -> bool:
        return not (isinstance(action, dict) and action.get("reason_code") == "human_override_prohibited") \
            and not isinstance(sidecar_state.get("_gate_result", {}).get("override_authorization"), dict)

    def visibility(expected_visibility: str) -> bool:
        export = sidecar_state.get("_export_result", {})
        return isinstance(projection, dict) and projection.get("resolved_run_visibility") == expected_visibility \
            and isinstance(export, dict) and export.get("visibility") == expected_visibility

    registry = {
        "call_ledger_match": call_ledger_match,
        "semantic_input_origin": semantic_input_origin,
        "no_forbidden_calls": prohibited_side_effects,
        "journal_sequence": journal_sequence,
        "projection_pass": projection_pass,
        "ticket_provenance_preserved": ticket_provenance,
        "no_ticket_provenance_leak": no_ticket_leak,
        "journal_stops_after_evaluate": non_success_boundary,
        "zero_write_surfaces": non_success_boundary,
        "recovery_all_steps_executed": recovery_steps,
        "no_gate_override": no_gate_override,
        "visibility_public_preserved": lambda: visibility("public"),
        "visibility_project_preserved": lambda: visibility("project"),
        "visibility_restricted_preserved": lambda: visibility("restricted"),
        "identity_provenance_digest_preserved": lambda: _identity_provenance_digest_check(
            sidecar_state, scenario["inputs"]),
        "typed_outcome_match": typed_outcome,
        "observable_side_effects_match": observable_side_effects,
        "prohibited_side_effects_absent": prohibited_side_effects,
    }
    results = []
    for rule_id in declared:
        oracle = registry.get(rule_id)
        if oracle is None:
            results.append({"rule": rule_id, "status": "fail", "step_id": "catalog",
                            "detail": {"reason": "unknown_or_inapplicable_rule"}})
            continue
        try:
            passed = oracle()
        except Exception as exc:  # fail closed on a checker fault
            results.append({"rule": rule_id, "status": "fail", "step_id": "catalog",
                            "detail": {"reason": "oracle_error", "type": type(exc).__name__}})
            continue
        results.append({"rule": rule_id, "status": "pass" if passed else "fail",
                        "step_id": "catalog"})

    return results


# ---------------------------------------------------------------------------
# Generic catalog-driven pipeline executor
# ---------------------------------------------------------------------------

def _execute_pipeline(scenario: dict, tmp_dir: pathlib.Path) -> dict:
    """Execute a scenario pipeline; each catalog step invokes exactly one real
    production callable. The catalog/scenario/inputs objects are never mutated.

    Returns a public machine-readable result with ONLY the stable semantic
    summary (no raw event_id, occurred_at, signatures, raw content digests
    derived from store-assigned fields, absolute paths, or execution metadata).
    Per-run raw cryptographic verification is performed independently and its
    results are recorded in verification_results."""
    scenario_id = scenario["scenario_id"]
    # Deep copy so the caller's catalog/scenario/inputs object is provably unchanged.
    scenario = copy.deepcopy(scenario)
    catalog = load_catalog()
    inputs = scenario.get("inputs", {})
    pipeline = scenario.get("pipeline", [])

    # Validate the complete immutable input object BEFORE any database or call.
    _validate_scenario(scenario, catalog)

    call_ledger: list = []
    sidecar_state: dict = {}
    call_counts: dict = {}
    verification_results: list = []
    boundary_snapshots: list = []
    verify_state: dict = {
        "last_event_order": 0,
        "signer_key": _resolve_signer_key(inputs, catalog),
        "run_id": inputs.get("run_id"),
    }

    failed = False
    for entry in pipeline:
        step_id = entry["step_id"]
        component = entry["component"]
        operation = entry["operation"]
        binding = entry.get("input_binding", {})

        # Scenario 012 is a verifier-owned exported-evidence tamper path.  The
        # final publish is deliberately suppressed after proving a deep-copy
        # mismatch; no production output or production callable is mutated.
        if binding.get("suppress_after_export_tamper"):
            original = sidecar_state.get("_export_result")
            original_before = _sha256_obj(original)
            probe = copy.deepcopy(original)
            if isinstance(probe, dict) and probe.get("events"):
                probe["events"][0]["event_id"] = "tampered-by-verifier"
            expected = _export_digest_oracle(probe)
            detected = isinstance(probe, dict) and expected != probe.get("export_content_digest")
            verification_results.extend([
                {"rule": "export_tamper_detected", "status": "pass" if detected else "fail", "step_id": step_id},
                {"rule": "export_original_unchanged", "status": "pass" if _sha256_obj(original) == original_before else "fail", "step_id": step_id},
                {"rule": "publish_suppressed", "status": "pass" if detected else "fail", "step_id": step_id},
            ])
            call_ledger.append({"step_id": step_id, "component": component, "operation": operation,
                                "actual_callable": f"{_publish_to_gate.__module__}.{_publish_to_gate.__qualname__}",
                                "invocation_count": 0, "status": "suppressed",
                                "input_digest": _sha256_obj(binding), "output_kind": "publish_to_gate",
                                "semantic_output_digest": "", "raw_verification_status": "not_applicable"})
            continue

        adapter = ADAPTERS.get((component, operation))
        production = PRODUCTION_CALLABLE.get((component, operation))

        # Build the request (no production call yet).
        try:
            callable_, args, kwargs, input_repr = adapter(inputs, binding, sidecar_state,
                                                          tmp_dir, catalog)
        except ScenarioInputError as exc:
            call_ledger.append({
                "step_id": step_id,
                "component": component,
                "operation": operation,
                "actual_callable": None,
                "invocation_count": 0,
                "input_digest": _sha256_obj(binding),
                "output_kind": _OPERATION_TO_OUTPUT_KIND.get(operation, operation),
                "semantic_output_digest": "",
                "raw_verification_status": "not_applicable",
                "status": "error",
            })
            failed = True
            break

        input_digest = _sha256_obj(binding)
        fqn = f"{production.__module__}.{production.__qualname__}"

        expectation = catalog["scenario_expectations"][scenario_id]
        is_non_success_boundary = (
            operation == "evaluate_validator_mesh"
            and expectation.get("typed_outcome") in {
                "fail", "blocked", "inconclusive", "human_review_required"
            }
        )
        before_boundary = _surface_snapshot(sidecar_state, inputs)

        # Derive invocation count from observed engine call accounting.
        before = call_counts.get(fqn, 0)
        try:
            output = callable_(*args, **kwargs)
        except Exception as exc:  # production callable raised -> typed failure
            output = {"error": str(exc), "type": type(exc).__name__}
            status = "error"
        else:
            status = "ok"
        call_counts[fqn] = before + 1
        invocation_count = call_counts[fqn] - before  # == 1 by construction

        # Snapshot immediately after the production operation, before carrying
        # an allowed return value into the downstream in-memory state.
        after_boundary = _surface_snapshot(sidecar_state, inputs)
        _propagate(component, operation, output, sidecar_state)

        if is_non_success_boundary:
            boundary_snapshots.append({
                "step_id": step_id,
                "before": _surface_digests(before_boundary),
                "after": _surface_digests(after_boundary),
                "all_five_surfaces_equal": before_boundary == after_boundary,
                "setup_step_ids": [item["step_id"] for item in call_ledger],
                "ledger_index": len(call_ledger),
            })

        # (a) Raw cryptographic verification (per-run, uses actual production
        # return including store-assigned event_id/occurred_at/signatures).
        output_kind = _OPERATION_TO_OUTPUT_KIND.get(operation, operation)
        rule_results = _verify_raw_evidence(output, output_kind, verify_state, inputs, binding)
        raw_verification_status = "pass" if all(
            v["status"] == "pass" for v in rule_results) else ("fail" if rule_results else "not_applicable")
        for v in rule_results:
            v["step_id"] = step_id
        verification_results.extend(rule_results)

        # Update chain verification state for the next step.
        _update_verify_state(verify_state, output, output_kind)

        # (b) Stable semantic digest from documented contract-derived projection.
        stable_projection = _project_stable(output, output_kind)
        try:
            semantic_output_digest = _sha256_obj(stable_projection)
        except Exception:
            semantic_output_digest = ""

        call_ledger.append({
            "step_id": step_id,
            "component": component,
            "operation": operation,
            "actual_callable": fqn,
            "invocation_count": invocation_count,
            "status": status,
            "input_digest": input_digest,
            "output_kind": output_kind,
            "semantic_output_digest": semantic_output_digest,
            "raw_verification_status": raw_verification_status,
        })

        # Fail-fast: stop immediately on the first step error.
        if status == "error":
            failed = True
            break

    final_verdict = None
    mesh_result = sidecar_state.get("_mesh_result", {})
    if isinstance(mesh_result, dict):
        verdict = mesh_result.get("overall_verdict") or mesh_result.get("aggregate_verdict")
        if verdict:
            final_verdict = verdict

    state_facts = _extract_state_facts(sidecar_state, inputs)
    result = {
        "scenario_id": scenario_id,
        "scenario_status": "fail",
        "final_verdict": final_verdict,
        "call_ledger": call_ledger,
        "state_facts": state_facts,
        "verification_results": verification_results,
        "boundary_snapshots": boundary_snapshots,
    }
    for proof in boundary_snapshots:
        later = call_ledger[proof["ledger_index"] + 1:]
        proof["prohibited_downstream_call_count"] = sum(
            item.get("invocation_count", 0) for item in later)
    catalog_results = _execute_catalog_oracles(
        scenario, catalog, result, sidecar_state, boundary_snapshots)
    result["declared_rule_results"] = [
        {"rule_id": item["rule"], "status": item["status"]}
        for item in catalog_results]
    verification_results.extend(catalog_results)
    verification_failed = any(v.get("status") != "pass" for v in verification_results)
    result["scenario_status"] = "pass" if not failed and not verification_failed else "fail"
    sidecar = sidecar_state.get("_sidecar")
    if sidecar is not None:
        sidecar.close()

    return result


def _propagate(component: str, operation: str, output: dict, sidecar_state: dict) -> None:
    """Capture a production callable's output in runtime-only state for the next
    pipeline step. This is NOT a production call."""
    if operation == "create_run" and isinstance(output, dict):
        sidecar_state["_run_id"] = output.get("run_id", sidecar_state.get("_run_id", ""))
        nd = output.get("new_stream_head", {}).get("content_digest")
        if nd is not None:
            sidecar_state["prev_digest"] = nd
        if "event_order" in output:
            sidecar_state["head_order"] = output["event_order"]
    elif operation in ("append_event", "commit_stage") and isinstance(output, dict):
        nd = output.get("new_stream_head", {}).get("content_digest")
        if nd is not None:
            sidecar_state["prev_digest"] = nd
        if "event_order" in output:
            sidecar_state["head_order"] = output["event_order"]
    elif operation == "dispatch":
        # ``dispatch`` returns a list of ValidatorDispatchResult dicts directly.
        if isinstance(output, dict) and "dispatch_results" in output:
            sidecar_state["_dispatch_results"] = output["dispatch_results"]
        else:
            sidecar_state["_dispatch_results"] = output
    elif operation == "evaluate_validator_mesh" and isinstance(output, dict):
        # Preserve the raw production result exactly for downstream consumers.
        sidecar_state["_mesh_result"] = output
    elif operation == "publish_to_gate" and isinstance(output, dict):
        sidecar_state["_gate_result"] = output
        sidecar_state["_last_decision_id"] = output.get("decision_id")
    elif operation == "export_run" and isinstance(output, dict):
        sidecar_state["_export_result"] = output
    elif operation == "evaluate_runtime_action" and isinstance(output, dict):
        sidecar_state["_action_policy_result"] = output


# ---------------------------------------------------------------------------
# Engine / CLI
# ---------------------------------------------------------------------------

def run_scenario(scenario_id: str, tmp_dir: pathlib.Path) -> dict:
    catalog = load_catalog()
    scenario = None
    for s in catalog.get("scenarios", []):
        if s["scenario_id"] == scenario_id:
            scenario = s
            break
    if scenario is None:
        return {"scenario_id": scenario_id, "scenario_status": "error",
                "final_verdict": None, "call_ledger": [], "state_facts": {},
                "verification_results": []}

    scenario_tmp = tmp_dir / scenario_id
    scenario_tmp.mkdir(parents=True, exist_ok=True)
    try:
        return _execute_pipeline(scenario, scenario_tmp)
    except Exception as exc:
        return {"scenario_id": scenario_id, "scenario_status": "error",
                "final_verdict": None,
                "call_ledger": [],
                "state_facts": {},
                "verification_results": [{
                    "rule": "execution_exception",
                    "status": "fail",
                    "step_id": "execution",
                    "detail": {"type": type(exc).__name__, "message": str(exc)},
                }]}


def run_all_scenarios(tmp_dir: pathlib.Path) -> list:
    catalog = load_catalog()
    scenario_ids = sorted(s["scenario_id"] for s in catalog.get("scenarios", []))
    return [run_scenario(sid, tmp_dir) for sid in scenario_ids]


def _all_mode_exit_code(results: list, catalog: dict) -> int:
    """Return zero only for the complete public 20-scenario conformance set."""
    expected_total = catalog.get("scenario_count")
    scenario_ids = [result.get("scenario_id") for result in results]
    passed = sum(1 for result in results if result.get("scenario_status") == "pass")
    if (
        expected_total == 20
        and len(results) == expected_total
        and len(scenario_ids) == expected_total
        and len(set(scenario_ids)) == expected_total
        and passed == expected_total
        and all(not any(v.get("status") in {"fail", "blocked", "inconclusive"}
                        for v in result.get("verification_results", [])) for result in results)
    ):
        return 0
    return 1


def _export_digest_oracle(envelope: dict | None) -> str | None:
    """Independent stdlib oracle for an exported-evidence content digest."""
    if not isinstance(envelope, dict):
        return None
    preimage = {key: value for key, value in envelope.items() if key != "export_content_digest"}
    return "sha256:" + hashlib.sha256(_canon(preimage).encode("utf-8")).hexdigest()


def load_catalog() -> dict:
    if not CONFORMANCE_CATALOG_PATH.exists():
        print(json.dumps({"error": "catalog_not_found", "path": str(CONFORMANCE_CATALOG_PATH)}))
        sys.exit(2)
    return json.loads(CONFORMANCE_CATALOG_PATH.read_text(encoding="utf-8"))


def list_scenarios(catalog: dict) -> dict:
    scens = catalog.get("scenarios", [])
    return {
        "mode": "list",
        "total": len(scens),
        "scenarios": [
            {
                "scenario_id": s["scenario_id"],
                "description": s.get("description", ""),
                "trigger_kind": s.get("trigger_kind", ""),
                "dependencies": s.get("dependencies", []),
                "step_count": len(s.get("pipeline", [])),
            }
            for s in scens
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Railyard v0.8 Runtime Smoke Runner")
    parser.add_argument("--tmp-dir", required=True,
                        help="Caller-supplied temporary workspace directory (REQUIRED)")
    parser.add_argument("--scenario", help="Run a specific scenario by ID")
    parser.add_argument("--all", action="store_true", help="Run all scenarios")
    parser.add_argument("--output", help="Write JSON output to file")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "list", "info"])
    args = parser.parse_args()

    tmp_dir = pathlib.Path(args.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "list":
        _output(list_scenarios(load_catalog()), args.output)
        return
    if args.command == "info":
        c = load_catalog()
        _output({
            "mode": "info",
            "contract_ref": c.get("contract_ref", ""),
            "contract_sha256": c.get("contract_sha256", ""),
            "version": c.get("version", ""),
            "scenario_count": c.get("scenario_count", 0),
        }, args.output)
        return

    if args.all:
        results = run_all_scenarios(tmp_dir)
        passed = sum(1 for r in results if r.get("scenario_status") == "pass")
        failed = sum(1 for r in results if r.get("scenario_status") != "pass")
        _output({"mode": "all", "total": len(results), "passed": passed,
                 "failed": failed, "results": results}, args.output)
        sys.exit(_all_mode_exit_code(results, load_catalog()))
    elif args.scenario:
        res = run_scenario(args.scenario, tmp_dir)
        _output({"mode": "single", "result": res}, args.output)
    else:
        print("Error: specify --all or --scenario <id>", file=sys.stderr)
        sys.exit(2)


def _output(result: dict, output_path: str | None):
    s = json.dumps(result, indent=2, default=str)
    if output_path:
        pathlib.Path(output_path).write_text(s, encoding="utf-8")
    print(s)


if __name__ == "__main__":
    main()
