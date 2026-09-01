"""Unittest harness for the Runtime Action Policy Contract v2.0.0 schema.

Validates the Draft 2020-12 encoding in
``assets/schemas/runtime-action-policy-v2.schema.json`` against the frozen
contract and the v2 conformance catalog. The external GateDecision snapshot
shape is resolved OFFLINE through a ``referencing.Registry`` that binds the
relative ``$ref`` target ``runtime-gate-decision-v2.schema.json`` to the frozen
gate schema on disk. No network access, no artifact generation, no writes.

Acceptance coverage:
  * ``Draft202012Validator.check_schema`` accepts the v2 schema.
  * ``$id`` is ``urn:railyard:schema:runtime-action-policy:v2``.
  * All external ``$ref``s resolve offline (gate decision binding).
  * Root ``oneOf`` exposes exactly the eight canonical public types.
  * Every object shape is closed (``additionalProperties: false``).
  * Catalog declarations: positives validate; negatives are single-cause with a
    reversible ``repaired_fixture``; ``schema_valid_contract_invalid`` fixtures
    are schema-valid.
  * Exact enum / root / branch set equality.
  * v1 predecessor immutability (frozen file digests unchanged).
  * Public ASCII hygiene across the five scoped files.
"""

import hashlib
import json
import os
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

V2_SCHEMA_PATH = os.path.join(ROOT, "assets", "schemas", "runtime-action-policy-v2.schema.json")
GATE_SCHEMA_PATH = os.path.join(ROOT, "assets", "schemas", "runtime-gate-decision-v2.schema.json")
V1_SCHEMA_PATH = os.path.join(ROOT, "assets", "schemas", "runtime-action-policy-v1.schema.json")
V2_CATALOG_PATH = os.path.join(ROOT, "examples", "runtime_action_policy_contract", "conformance-v2.json")
V1_CATALOG_PATH = os.path.join(ROOT, "examples", "runtime_action_policy_contract", "conformance.json")
INVENTORY_JSON_PATH = os.path.join(ROOT, "references", "governance-document-inventory.json")
INVENTORY_MD_PATH = os.path.join(ROOT, "references", "governance-document-inventory.md")

EXPECTED_V2_ID = "urn:railyard:schema:runtime-action-policy:v2"

ROOT_PUBLIC_TYPES = [
    "RuntimeActionPolicyDeclaration",
    "RuntimeBoundaryFacts",
    "RetryEligibility",
    "CheckpointEvidence",
    "ActionAuthorization",
    "RuntimeActionRequest",
    "RuntimeActionDecision",
    "RuntimeActionEvaluationError",
]

BRANCH_TYPES = [
    "RuntimeActionRequest_stop_stage",
    "RuntimeActionRequest_stop_run",
    "RuntimeActionRequest_retry",
    "RuntimeActionRequest_resume",
    "RuntimeActionRequest_more_evidence",
    "RuntimeActionRequest_redesign",
    "RuntimeActionRequest_human_intervention",
    "RuntimeActionRequest_terminate",
]

EXPECTED_ACTION_KINDS = [
    "stop_stage", "stop_run", "retry", "resume",
    "more_evidence", "redesign", "human_intervention", "terminate",
]

EXPECTED_ERROR_CODES = [
    "unknown_action_kind",
    "authorization_missing",
    "authorization_role_invalid",
    "checkpoint_evidence_invalid",
    "invalid_action_branch",
    "lineage_self_reference",
    "child_lineage_parent_mismatch",
    "gate_snapshot_contradiction",
    "system_retry_unauthorized",
    "policy_exhausted_unsupported",
    "human_override_prohibited",
    "history_rewrite_prohibited",
]

EXPECTED_AUTHORIZED_REASON_CODES = [
    "action_authorized_stop_stage",
    "action_authorized_stop_run",
    "action_authorized_retry",
    "action_authorized_resume",
    "action_authorized_more_evidence",
    "action_authorized_redesign",
    "action_authorized_human_intervention",
    "action_authorized_terminate",
]

EXPECTED_DENIED_REASON_CODES = [
    "denied_parent_status_ineligible",
    "denied_stage_not_active",
    "denied_gate_recommendation_mismatch",
    "denied_retry_bounds_exceeded",
    "denied_checkpoint_unavailable",
    "denied_evidence_gap_unrecoverable",
    "denied_terminal_run",
]

EXPECTED_CATALOG_COUNTS = {
    "positives": 45,
    "negatives": 18,
    "schema_valid_contract_invalid": 14,
    "authorized_reason_codes": 8,
    "denied_reason_codes": 7,
    "error_codes": 12,
    "action_kinds": 8,
    "root_public_types": 8,
}

# Frozen predecessor digests (must remain unchanged by this runner).
FROZEN_V1_SCHEMA_SHA256 = "92a3f78118565f8fc03d0513d6ca35af93e45171a8ea638bff6fa85ac55625cb"
FROZEN_V1_CATALOG_SHA256 = "5136a989a063da45280df17c05ac51533e3d0ac010b914824a7864b75c1f92b6"
FROZEN_GATE_SCHEMA_SHA256 = "32cd278b25bd348cb9e810cec27337f72d9bfceef43f01c50c8bcddfc280264a"

EXPECTED_ACTIVE_GOVERNANCE_ENTRIES = {
    "references/runtime-action-policy-contract.md": {
        "document_id": "railyard-runtime-action-policy-contract-v2.0.0",
        "governance_kind": "contract",
        "version": "2.0.0",
        "authority_level": "canonical",
        "overrideability": "non_overridable",
        "canonical_for": ["runtime-action-policy-contract"],
    },
    "assets/schemas/runtime-action-policy-v2.schema.json": {
        "document_id": "railyard-runtime-action-policy-schema-v2.0.0",
        "governance_kind": "schema",
        "version": "2.0.0",
        "authority_level": "canonical",
        "overrideability": "non_overridable",
        "canonical_for": ["runtime-action-policy-schema"],
    },
}


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _build_registry():
    gate = _load_json(GATE_SCHEMA_PATH)
    return Registry().with_resources([
        ("runtime-gate-decision-v2.schema.json",
         Resource(contents=gate, specification=DRAFT202012)),
    ])


def _structural_leaf_difference_paths(left, right, path="$"):
    """Return structural leaf paths whose values differ between JSON values.

    Missing compound values expand to their descendant leaves, so inserting or
    removing a whole object cannot be misreported as one atomic mutation.
    """
    if isinstance(left, dict) and isinstance(right, dict):
        differences = []
        for key in sorted(set(left) | set(right)):
            child_path = "%s.%s" % (path, key)
            if key not in left:
                differences.extend(_structural_leaf_paths(right[key], child_path))
            elif key not in right:
                differences.extend(_structural_leaf_paths(left[key], child_path))
            else:
                differences.extend(
                    _structural_leaf_difference_paths(left[key], right[key], child_path)
                )
        return differences
    if isinstance(left, list) and isinstance(right, list):
        differences = []
        for index in range(max(len(left), len(right))):
            child_path = "%s[%d]" % (path, index)
            if index >= len(left):
                differences.extend(_structural_leaf_paths(right[index], child_path))
            elif index >= len(right):
                differences.extend(_structural_leaf_paths(left[index], child_path))
            else:
                differences.extend(
                    _structural_leaf_difference_paths(left[index], right[index], child_path)
                )
        return differences
    return [] if left == right else [path]


def _structural_leaf_paths(value, path):
    if isinstance(value, dict):
        if not value:
            return [path]
        return [
            child_path
            for key in sorted(value)
            for child_path in _structural_leaf_paths(value[key], "%s.%s" % (path, key))
        ]
    if isinstance(value, list):
        if not value:
            return [path]
        return [
            child_path
            for index, item in enumerate(value)
            for child_path in _structural_leaf_paths(item, "%s[%d]" % (path, index))
        ]
    return [path]


class SchemaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = _load_json(V2_SCHEMA_PATH)
        cls.registry = _build_registry()
        cls.validator = Draft202012Validator(cls.schema, registry=cls.registry)
        cls.catalog = _load_json(V2_CATALOG_PATH)

    def test_check_schema_accepts_v2(self):
        Draft202012Validator.check_schema(self.schema)

    def test_schema_id(self):
        self.assertEqual(self.schema.get("$id"), EXPECTED_V2_ID)

    def test_root_oneof_exposes_eight_types(self):
        root_oneof = self.schema.get("oneOf", [])
        self.assertEqual(len(root_oneof), 8)
        resolved = []
        for sub in root_oneof:
            ref = sub.get("$ref", "")
            self.assertTrue(ref.startswith("#/$defs/"), ref)
            name = ref.split("/")[-1]
            resolved.append(name)
        self.assertEqual(sorted(resolved), sorted(ROOT_PUBLIC_TYPES))

    def test_branch_oneof_exposes_eight_branches(self):
        request_def = self.schema["$defs"]["RuntimeActionRequest"]
        branch_oneof = request_def.get("oneOf", [])
        self.assertEqual(len(branch_oneof), 8)
        resolved = [b.get("$ref", "").split("/")[-1] for b in branch_oneof]
        self.assertEqual(sorted(resolved), sorted(BRANCH_TYPES))

    def test_every_object_shape_closed(self):
        def walk(node, path):
            if not isinstance(node, dict):
                return
            # A standalone object shape (explicitly typed object) must be closed.
            # The root is a oneOf dispatcher and is intentionally open at the
            # instance level; nested allOf/if-then refinement subschemas merge
            # into a typed parent and need not restate the closure themselves.
            if node.get("type") == "object" and "properties" in node:
                self.assertIn("additionalProperties", node,
                              "missing additionalProperties at %s" % path)
                self.assertFalse(node["additionalProperties"],
                                 "additionalProperties not false at %s" % path)
            for key, value in node.items():
                if isinstance(value, dict):
                    walk(value, path + "/" + key)
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        if isinstance(item, dict):
                            walk(item, "%s/%s/%d" % (path, key, index))

        walk(self.schema, "#")
        for name, definition in self.schema["$defs"].items():
            walk(definition, "#/$defs/" + name)

    def test_offline_gate_ref_resolves(self):
        # A stop_stage request carries a GateDecision snapshot; resolution must
        # succeed entirely from the in-memory registry (no network).
        binding = {
            "source_gate_decision_ref": {
                "artifact_id": "gd-1", "artifact_kind": "gate-decision",
                "digest": "sha256:" + "a" * 64,
            },
            "gate_decision_snapshot": {
                "decision_id": "gd-1", "gate_id": "gate-1", "outcome": "fail",
                "execution_mode": "full",
                "evidence": [{"artifact_id": "ev-1", "artifact_kind": "evidence"}],
                "recommendation": "stop_stage",
                "failure_code": "validator_fail_deterministic",
                "failure_description": "Validator found deterministic error-severity failures.",
                "evaluated_at": "2026-01-01T00:00:00Z", "evaluated_by": "gate-eval",
                "run_context": {"run_id": "run-1", "stage_id": "stage-1"},
            },
            "canonical_digest": "sha256:" + "a" * 64,
        }
        binding_validator = self.validator.evolve(schema=self.schema["$defs"]["GateSnapshotBinding"])
        errors = list(binding_validator.iter_errors(binding))
        self.assertEqual(errors, [], [e.message for e in errors])

    def test_enum_set_equality(self):
        action_kind_enum = self.schema["$defs"]["RuntimeActionRequest_stop_stage"]["properties"]["action_kind"]
        # action_kind appears on every branch with const; the union is encoded
        # across the eight const values. Verify each branch const is expected.
        request_def = self.schema["$defs"]["RuntimeActionRequest"]
        branch_consts = []
        for branch in request_def["oneOf"]:
            name = branch["$ref"].split("/")[-1]
            const = self.schema["$defs"][name]["properties"]["action_kind"]["const"]
            branch_consts.append(const)
        self.assertEqual(sorted(branch_consts), sorted(EXPECTED_ACTION_KINDS))

        err_enum = self.schema["$defs"]["RuntimeActionEvaluationError"]["properties"]["error_code"]["enum"]
        self.assertEqual(err_enum, EXPECTED_ERROR_CODES)

        decision_reason_enum = self.schema["$defs"]["RuntimeActionDecision"]["properties"]["reason_code"]["enum"]
        self.assertEqual(
            sorted(decision_reason_enum),
            sorted(EXPECTED_AUTHORIZED_REASON_CODES + EXPECTED_DENIED_REASON_CODES),
        )


class CatalogDeclarationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = _load_json(V2_SCHEMA_PATH)
        cls.registry = _build_registry()
        cls.validator = Draft202012Validator(cls.schema, registry=cls.registry)
        cls.catalog = _load_json(V2_CATALOG_PATH)

    def _assert_valid(self, fixture, label):
        errors = list(self.validator.iter_errors(fixture))
        self.assertEqual(errors, [], "%s: %s" % (label, [e.message for e in errors]))

    def test_positive_declarations(self):
        for entry in self.catalog["positives"]:
            self._assert_valid(entry["fixture"], "positive:%s" % entry["id"])

    def test_negative_single_cause_reversible(self):
        for entry in self.catalog["negatives"]:
            invalid = entry["invalid_fixture"]
            repaired = entry["repaired_fixture"]
            invalid_errors = list(self.validator.iter_errors(invalid))
            self.assertTrue(invalid_errors,
                            "negative:%s invalid_fixture unexpectedly valid" % entry["id"])
            # repaired form must validate (reversibility).
            repaired_errors = list(self.validator.iter_errors(repaired))
            self.assertEqual(repaired_errors, [],
                             "negative:%s repaired_fixture invalid: %s"
                             % (entry["id"], [e.message for e in repaired_errors]))

    def test_negative_repairs_change_exactly_one_structural_leaf(self):
        for entry in self.catalog["negatives"]:
            difference_paths = _structural_leaf_difference_paths(
                entry["invalid_fixture"], entry["repaired_fixture"]
            )
            self.assertEqual(
                len(difference_paths),
                1,
                "negative:%s must differ by exactly one atomic leaf, got %s"
                % (entry["id"], difference_paths),
            )

    def test_schema_valid_contract_invalid(self):
        for entry in self.catalog["schema_valid_contract_invalid"]:
            self._assert_valid(entry["fixture"], "svc:%s" % entry["id"])

    def test_catalog_counts_consistent(self):
        counts = self.catalog["counts"]
        self.assertEqual(counts, EXPECTED_CATALOG_COUNTS)
        self.assertEqual(counts["positives"], len(self.catalog["positives"]))
        self.assertEqual(counts["negatives"], len(self.catalog["negatives"]))
        self.assertEqual(counts["schema_valid_contract_invalid"],
                         len(self.catalog["schema_valid_contract_invalid"]))
        self.assertEqual(counts["root_public_types"], 8)
        self.assertEqual(counts["action_kinds"], 8)
        self.assertEqual(counts["error_codes"], 12)
        self.assertEqual(counts["authorized_reason_codes"], 8)
        self.assertEqual(counts["denied_reason_codes"], 7)

    def test_catalog_enums_consistent(self):
        enums = self.catalog["enums"]
        self.assertEqual(sorted(enums["authorized_reason_codes"]), sorted(EXPECTED_AUTHORIZED_REASON_CODES))
        self.assertEqual(sorted(enums["denied_reason_codes"]), sorted(EXPECTED_DENIED_REASON_CODES))
        self.assertEqual(enums["error_codes"], EXPECTED_ERROR_CODES)
        self.assertEqual(sorted(enums["action_kinds"]), sorted(EXPECTED_ACTION_KINDS))


class PredecessorImmutabilityTests(unittest.TestCase):
    def test_v1_schema_digest_unchanged(self):
        self.assertEqual(_sha256(V1_SCHEMA_PATH), FROZEN_V1_SCHEMA_SHA256)

    def test_v1_catalog_digest_unchanged(self):
        self.assertEqual(_sha256(V1_CATALOG_PATH), FROZEN_V1_CATALOG_SHA256)

    def test_gate_schema_digest_unchanged(self):
        self.assertEqual(_sha256(GATE_SCHEMA_PATH), FROZEN_GATE_SCHEMA_SHA256)

class GovernanceInventoryTests(unittest.TestCase):
    def test_active_runtime_action_policy_entries_are_exact_and_unique(self):
        inv = _load_json(INVENTORY_JSON_PATH)
        documents = inv.get("documents", inv)
        for expected_path, expected_metadata in EXPECTED_ACTIVE_GOVERNANCE_ENTRIES.items():
            expected_topic = expected_metadata["canonical_for"]
            active_canonical = [
                document for document in documents
                if document.get("metadata", {}).get("status") == "active"
                and document.get("metadata", {}).get("canonical_for") == expected_topic
            ]
            self.assertEqual(
                len(active_canonical), 1,
                "expected exactly one active canonical entry for %s" % expected_topic,
            )
            entry = active_canonical[0]
            self.assertEqual(entry.get("path"), expected_path)
            metadata = entry.get("metadata", {})
            for field, expected_value in expected_metadata.items():
                self.assertEqual(metadata.get(field), expected_value, field)


class AsciiHygieneTests(unittest.TestCase):
    SCOPED_FILES = [
        V2_SCHEMA_PATH,
        V2_CATALOG_PATH,
        os.path.join(ROOT, "scripts", "test_runtime_action_policy_schema.py"),
        INVENTORY_JSON_PATH,
        os.path.join(ROOT, "references", "governance-document-inventory.md"),
    ]

    def test_scoped_files_ascii(self):
        for path in self.SCOPED_FILES:
            with open(path, "rb") as handle:
                data = handle.read()
            try:
                data.decode("ascii")
            except UnicodeDecodeError:
                self.fail("non-ASCII content in scoped file: %s" % path)


class MaxRetriesStructuralBoundaryTests(unittest.TestCase):
    """Structural-only boundary checks for ``max_retries`` on the retry branch.

    These assert the SCHEMA domain only: ``max_retries`` is a non-null integer
    ``>= 0`` with no schema maximum, for both ``RuntimeBoundaryFacts`` and the
    ``retry`` request-branch refinement. They intentionally do NOT evaluate
    policy eligibility (1..3) or the ``denied_retry_bounds_exceeded`` decision;
    that is the evaluator's job.     Boundary categories (structural domain only):
    -1/true/false/float/string/null/missing are SCHEMA-INVALID; 0/1/3/4/large
    are SCHEMA-VALID.
    """

    @classmethod
    def setUpClass(cls):
        cls.schema = _load_json(V2_SCHEMA_PATH)
        cls.registry = _build_registry()
        cls.validator = Draft202012Validator(cls.schema, registry=cls.registry)

    @staticmethod
    def _base_retry(max_retries_value, omit=False):
        fixture = {
            "policy_declaration": {
                "contract_id": "runtime-action-policy-contract",
                "contract_version": "2.0.0",
                "policy_id": "P-ACT-1",
                "evaluated_under": "runtime-action-policy-evaluator-v2",
            },
            "decision_id": "dec-1",
            "evaluated_at": "2026-01-01T00:00:00Z",
            "evaluated_by": "architect",
            "run_id": "run-1",
            "action_kind": "retry",
            "proposed_child_run_id": "c-1",
            "retry_strategy": "full",
            "failure_category": "command_failed",
            "authorization": {
                "authorized_by": "architect",
                "authorized_at": "2026-01-01T00:00:00Z",
                "authorization_id": "az-1",
                "reason": "Authorized.",
            },
            "boundary_facts": {
                "parent_run_id": "run-1",
                "parent_run_status": "failed",
                "current_retry_count": 0,
                "same_kind_failure_count": 0,
                "attempt_history_facts": {
                    "attempt_count": 1,
                    "last_failure_category": "command_failed",
                    "last_failure_transient": True,
                    "last_failure_deterministic": True,
                },
            },
        }
        if not omit:
            fixture["boundary_facts"]["max_retries"] = max_retries_value
        return fixture

    def test_max_retries_structural_categories(self):
        retry_validator = self.validator.evolve(
            schema=self.schema["$defs"]["RuntimeActionRequest_retry"]
        )
        # (value, expected_schema_valid, note)
        cases = [
            (-1, False, "below structural minimum 0"),
            (0, True, "non-null integer >= 0 (structurally valid; policy-ineligible)"),
            (1, True, "valid and eligible"),
            (3, True, "valid and eligible"),
            (4, True, "structurally valid; policy-ineligible"),
            (1000, True, "structurally valid; policy-ineligible (large integer)"),
            (True, False, "boolean not integer"),
            (False, False, "boolean not integer"),
            (1.5, False, "float not integer"),
            ("five", False, "string not integer"),
            (None, False, "null not non-null integer"),
        ]
        for value, expect_valid, note in cases:
            fixture = self._base_retry(value)
            errors = list(retry_validator.iter_errors(fixture))
            if expect_valid:
                self.assertEqual(
                    errors, [],
                    "max_retries=%r should be SCHEMA-VALID (%s): %s"
                    % (value, note, [e.message for e in errors]),
                )
            else:
                self.assertTrue(
                    errors,
                    "max_retries=%r should be SCHEMA-INVALID (%s)" % (value, note),
                )
        # missing case
        missing = self._base_retry(None, omit=True)
        self.assertTrue(
            list(retry_validator.iter_errors(missing)),
            "max_retries missing should be SCHEMA-INVALID",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
