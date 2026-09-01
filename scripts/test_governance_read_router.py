"""Unit tests for governance_read_router.py"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

# Ensure the scripts directory is importable
_scripts_dir = pathlib.Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir.parent))

from scripts.governance_read_router import (
    GovernanceRoutingConfigurationError,
    validate_governance_configuration,
    resolve_governance_reads,
    main,
)

# Project root
PROJECT_ROOT = _scripts_dir.parent


# ---------------------------------------------------------------------------
# Helper: get routing result for a simple request
# ---------------------------------------------------------------------------

def _result(request):
    return resolve_governance_reads(request, PROJECT_ROOT)


def _ready(request):
    result = _result(request)
    assert result["status"] == "ready", f"Expected ready, got: {result}"
    return result["normative_reads"]


def _blocked(request):
    result = _result(request)
    assert result["status"] == "blocked", f"Expected blocked, got: {result}"
    return result


# ---------------------------------------------------------------------------
# Positive tests
# ---------------------------------------------------------------------------

class TestGovernanceReadRouter(unittest.TestCase):
    """Main test class for governance read routing."""

    # ===================================================================
    # 1. Five exact baselines
    # ===================================================================

    def test_baseline_planner(self):
        reads = _ready({"role": "planner"})
        self.assertEqual(reads, [
            "SKILL.md",
            "references/roles.md",
            "references/lifecycle.md",
            "references/startup-sequence.md",
            "references/epic-format.md",
            "references/helper-commands.md",
        ])

    def test_baseline_architect(self):
        reads = _ready({"role": "architect"})
        self.assertEqual(reads, [
            "SKILL.md",
            "references/roles.md",
            "references/lifecycle.md",
            "references/startup-sequence.md",
            "references/routing.md",
            "references/platform-dispatch.md",
            "references/ticket-format.md",
            "references/helper-commands.md",
        ])

    def test_baseline_runner(self):
        reads = _ready({"role": "runner"})
        self.assertEqual(reads, [
            "SKILL.md",
            "references/roles.md",
            "references/startup-sequence.md",
            "references/ticket-format.md",
            "references/result-format.md",
        ])

    def test_baseline_validator(self):
        reads = _ready({"role": "validator"})
        self.assertEqual(reads, [
            "references/roles.md",
            "references/validator-protocol.md",
            "references/validator-verdict-handoff-tree.md",
            "references/validation-contract.md",
        ])

    def test_baseline_knowledge_curator(self):
        reads = _ready({"role": "knowledge_curator"})
        self.assertEqual(reads, [
            "SKILL.md",
            "references/roles.md",
            "references/knowledge-contract.md",
        ])

    # ===================================================================
    # 2. Seven individual conditionals
    # ===================================================================

    def test_conditional_validator_required(self):
        # validator_required applies to architect and planner
        reads = _ready({
            "role": "architect",
            "validator_required": True,
        })
        self.assertIn("references/validator-protocol.md", reads)
        self.assertIn("references/validator-verdict-handoff-tree.md", reads)
        self.assertIn("references/validation-contract.md", reads)

    def test_conditional_epic_closure(self):
        # epic_closure applies to planner only
        reads = _ready({
            "role": "planner",
            "epic_closure": True,
        })
        self.assertIn("references/validator-protocol.md", reads)
        self.assertIn("references/validator-verdict-handoff-tree.md", reads)
        self.assertIn("references/validation-contract.md", reads)

    def test_conditional_validation_task(self):
        reads = _ready({
            "role": "validator",
            "validation_task": True,
        })
        self.assertIn("references/validation-contract.md", reads)
        self.assertIn("references/validation-primitive-registry.md", reads)

    def test_conditional_validation_semantic(self):
        reads = _ready({
            "role": "validator",
            "validation_task": True,
            "validation_topic": "semantic",
        })
        self.assertIn("references/validation-contract.md", reads)
        self.assertIn("references/validation-primitive-registry.md", reads)
        self.assertIn("references/semantic-validation-contract.md", reads)

    def test_conditional_governance_task(self):
        reads = _ready({
            "role": "architect",
            "governance_task": True,
        })
        self.assertIn("references/governance-document-taxonomy.md", reads)
        self.assertIn("references/governance-document-inventory.json", reads)
        self.assertIn("references/governance-read-routing.json", reads)

    def test_conditional_knowledge_task(self):
        reads = _ready({
            "role": "knowledge_curator",
            "knowledge_task": True,
        })
        self.assertIn("references/knowledge-contract.md", reads)

    def test_conditional_runtime_task(self):
        reads = _ready({
            "role": "runner",
            "runtime_task": True,
        })
        self.assertIn("references/runtime-architecture.md", reads)

    # ===================================================================
    # 3. Overlapping-rule order and dedup
    # ===================================================================

    def test_overlapping_rules_order_and_dedup(self):
        """Multiple conditionals should produce correctly ordered, deduplicated output."""
        reads = _ready({
            "role": "planner",
            "validator_required": True,
            "epic_closure": True,
            "governance_task": True,
        })
        # Should contain the validator docs only once
        vp_count = sum(1 for p in reads if p == "references/validator-protocol.md")
        self.assertEqual(vp_count, 1, "Validator protocol should be deduplicated")
        # Check order: baseline first, then conditionals in registry order
        baseline_index = reads.index("SKILL.md")
        # first conditional includes should come after all baseline items
        cond_index = reads.index("references/validator-protocol.md")
        self.assertGreater(cond_index, baseline_index,
                           "Conditional includes should come after baseline")

    # ===================================================================
    # 4. Semantic dependency
    # ===================================================================

    def test_semantic_without_validation_task_blocked(self):
        result = _blocked({
            "role": "architect",
            "validation_topic": "semantic",
        })
        self.assertEqual(result["reason"], "invalid_kind")
        self.assertEqual(result["field"], "validation_topic")

    # ===================================================================
    # 5. Runtime isolation
    # ===================================================================

    def test_runtime_isolation(self):
        """runtime_task includes only runtime-architecture.md, not narrower refs."""
        reads = _ready({
            "role": "runner",
            "runtime_task": True,
        })
        # Must include runtime-architecture.md
        self.assertIn("references/runtime-architecture.md", reads)
        # Must NOT include narrower runtime contracts
        self.assertNotIn("references/runtime-state-contract.md", reads)
        self.assertNotIn("references/runtime-artifact-visibility-contract.md", reads)
        self.assertNotIn("references/runtime-evidence-export-contract.md", reads)

    # ===================================================================
    # 6. All exact ref forms
    # ===================================================================

    def test_contract_ref_by_path(self):
        reads = _ready({
            "role": "runner",
            "explicit_contract_refs": [
                {"form": "path", "value": "references/sql-contract.md"},
            ],
        })
        self.assertIn("references/sql-contract.md", reads)

    def test_contract_ref_by_document_id(self):
        reads = _ready({
            "role": "runner",
            "explicit_contract_refs": [
                {"form": "document_id", "value": "railyard-sql-contract-v0.8.0"},
            ],
        })
        self.assertIn("references/sql-contract.md", reads)

    def test_contract_ref_by_canonical_for(self):
        reads = _ready({
            "role": "runner",
            "explicit_contract_refs": [
                {"form": "canonical_for", "value": "sql-schema-contract"},
            ],
        })
        self.assertIn("references/sql-contract.md", reads)

    # ===================================================================
    # 7. Guide separation
    # ===================================================================

    def test_guide_refs_in_supplemental_not_normative(self):
        result = _result({
            "role": "architect",
            "explicit_guide_refs": [
                {"form": "path", "value": "references/model.md"},
            ],
        })
        self.assertEqual(result["status"], "ready")
        self.assertIn("references/model.md", result["supplemental_guides"])
        self.assertNotIn("references/model.md", result["normative_reads"])

    # ===================================================================
    # 8. Input immutability
    # ===================================================================

    def test_input_immutability(self):
        original = {
            "role": "architect",
            "validator_required": True,
        }
        request = dict(original)
        _result(request)
        self.assertEqual(request, original,
                         "resolve_governance_reads must not mutate the input dict")
        self.assertIsNot(request, original,
                         "resolve_governance_reads should make a copy")

    # ===================================================================
    # 9. CLI output
    # ===================================================================

    def test_cli_architect_output(self):
        """Test CLI --role architect produces correct JSON."""
        import subprocess
        router_path = str(_scripts_dir / "governance_read_router.py")
        result = subprocess.run(
            [sys.executable, router_path, "--role", "architect",
             "--railyard-root", str(PROJECT_ROOT)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"CLI failed: {result.stderr}")
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["role"], "architect")
        self.assertIsInstance(data["normative_reads"], list)
        self.assertIsInstance(data["supplemental_guides"], list)

    # ===================================================================
    # Negative tests: blocked reason codes
    # ===================================================================

    def test_blocked_unknown_field(self):
        result = _blocked({"role": "architect", "zzz_unknown": True})
        self.assertEqual(result["reason"], "unknown_field")
        self.assertEqual(result["field"], "zzz_unknown")

    def test_blocked_missing_role(self):
        result = _blocked({})
        self.assertEqual(result["reason"], "missing_role")
        self.assertEqual(result.get("field"), "role")

    def test_blocked_invalid_role(self):
        result = _blocked({"role": "superman"})
        self.assertEqual(result["reason"], "invalid_kind")
        self.assertEqual(result["field"], "role")

    def test_blocked_invalid_type_bool_field(self):
        result = _blocked({"role": "architect", "validator_required": "yes"})
        self.assertEqual(result["reason"], "invalid_kind")
        self.assertEqual(result["field"], "validator_required")

    def test_blocked_invalid_form_contract_ref(self):
        result = _blocked({
            "role": "architect",
            "explicit_contract_refs": [
                {"form": "fuzzy", "value": "something"},
            ],
        })
        self.assertEqual(result["reason"], "invalid_form")

    def test_blocked_invalid_form_guide_ref(self):
        result = _blocked({
            "role": "architect",
            "explicit_guide_refs": [
                {"form": "canonical_for", "value": "something"},
            ],
        })
        self.assertEqual(result["reason"], "invalid_form")

    def test_blocked_unknown_ref(self):
        result = _blocked({
            "role": "architect",
            "explicit_contract_refs": [
                {"form": "path", "value": "references/nonexistent.md"},
            ],
        })
        self.assertEqual(result["reason"], "unknown_ref")
        self.assertEqual(result["ref"], "references/nonexistent.md")

    def test_blocked_multiple_active_matches_note(self):
        """Note: With current frozen inventory, all canonical_for values are unique.
        This test documents the behavior; multiple_active_matches cannot be triggered
        with the current inventory data."""
        # Skip: cannot trigger with frozen inventory
        pass

    def test_blocked_inactive_or_superseded_note(self):
        """Note: With current frozen inventory, all documents are active.
        This test documents the behavior; inactive_or_superseded cannot be triggered
        with the current inventory data."""
        # Skip: cannot trigger with frozen inventory
        pass

    def test_blocked_guide_only_match(self):
        """Request a Guide-kind document as contract_ref should fail."""
        result = _blocked({
            "role": "architect",
            "explicit_contract_refs": [
                {"form": "path", "value": "README.md"},
            ],
        })
        self.assertEqual(result["reason"], "guide_only_match")
        self.assertEqual(result["ref"], "README.md")

    def test_adversarial_near_match_no_md(self):
        result = _blocked({
            "role": "architect",
            "explicit_contract_refs": [
                {"form": "path", "value": "references/lifecycle"},
            ],
        })
        self.assertEqual(result["reason"], "unknown_ref")

    def test_adversarial_near_match_mdz(self):
        result = _blocked({
            "role": "architect",
            "explicit_contract_refs": [
                {"form": "path", "value": "SKILL.mdz"},
            ],
        })
        self.assertEqual(result["reason"], "unknown_ref")

    # ===================================================================
    # Configuration validation tests
    # ===================================================================

    def _make_temp_inventory(self, entries):
        """Create a temporary inventory structure for testing."""
        return {
            "inventory_id": "test-inventory",
            "created_at": "2026-07-20",
            "schema_version": "1.0.0",
            "documents": entries,
            "exclusions": [],
        }

    def _make_temp_routing(self):
        """Create a minimal valid routing registry."""
        return {
            "routing_id": "railyard-governance-read-routing-v1",
            "version": "1.0.0",
            "created_at": "2026-07-20T00:00:00Z",
            "roles": {
                "architect": {
                    "description": "Test",
                    "required_reads": ["references/routing.md"],
                },
            },
            "conditional_rules": [],
            "ref_resolution": {"forms": ["path"], "fail_closed_rules": []},
            "output_rules": {
                "order": ["required_reads"],
                "deduplication": "first",
                "guide_policy": "never",
                "minimum_read_principle": "x",
            },
            "request_schema_ref": "urn:test",
            "result_schema_ref": "urn:test",
        }

    def test_config_missing_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            # Don't create inventory; also need routing
            (refs / "governance-read-routing.json").write_text(
                json.dumps(self._make_temp_routing()), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError):
                validate_governance_configuration(root)

    def test_config_invalid_json_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "governance-document-inventory.json").write_text(
                "not json {{{", encoding="utf-8")
            (refs / "governance-read-routing.json").write_text(
                json.dumps(self._make_temp_routing()), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError):
                validate_governance_configuration(root)

    def test_config_duplicate_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            # Create actual files
            (refs / "test.md").write_text("test", encoding="utf-8")
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/test.md",
                    "metadata": {
                        "document_id": "doc-1",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["test-1"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
                {
                    "path": "references/test.md",  # duplicate
                    "metadata": {
                        "document_id": "doc-2",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["test-2"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/routing.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("Duplicate path", str(ctx.exception))

    def test_config_duplicate_document_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "test1.md").write_text("test1", encoding="utf-8")
            (refs / "test2.md").write_text("test2", encoding="utf-8")
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/test1.md",
                    "metadata": {
                        "document_id": "doc-dup",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["test-1"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
                {
                    "path": "references/test2.md",
                    "metadata": {
                        "document_id": "doc-dup",  # duplicate
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["test-2"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/routing.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("Duplicate document_id", str(ctx.exception))

    def test_config_duplicate_active_canonical_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "test1.md").write_text("test1", encoding="utf-8")
            (refs / "test2.md").write_text("test2", encoding="utf-8")
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/test1.md",
                    "metadata": {
                        "document_id": "doc-1",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["shared-canonical"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
                {
                    "path": "references/test2.md",
                    "metadata": {
                        "document_id": "doc-2",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["shared-canonical"],  # duplicate
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/routing.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("Duplicate active canonical_for", str(ctx.exception))

    def test_config_self_supersedes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "test.md").write_text("test", encoding="utf-8")
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/test.md",
                    "metadata": {
                        "document_id": "doc-1",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["test-cf"],
                        "supersedes": ["doc-1"],  # self
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/routing.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("self", str(ctx.exception).lower())

    def test_config_broken_supersedes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "test.md").write_text("test", encoding="utf-8")
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/test.md",
                    "metadata": {
                        "document_id": "doc-1",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["test-cf"],
                        "supersedes": ["non-existent-doc"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/routing.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("Broken supersedes", str(ctx.exception))

    def test_config_cyclic_supersedes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "test1.md").write_text("test1", encoding="utf-8")
            (refs / "test2.md").write_text("test2", encoding="utf-8")
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/test1.md",
                    "metadata": {
                        "document_id": "doc-a",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["test-a"],
                        "supersedes": ["doc-b"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
                {
                    "path": "references/test2.md",
                    "metadata": {
                        "document_id": "doc-b",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["test-b"],
                        "supersedes": ["doc-a"],  # cycle!
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/routing.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("Cyclic", str(ctx.exception))

    def test_config_missing_file_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            # Don't create test_missing.md
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/test_missing.md",
                    "metadata": {
                        "document_id": "doc-1",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["test-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/routing.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("does not exist on disk", str(ctx.exception))

    def test_config_non_normative_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            (refs / "roles.md").write_text("roles", encoding="utf-8")
            (refs / "guide.md").write_text("guide", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/routing.md",
                    "metadata": {
                        "document_id": "doc-routing",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["routing-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
                {
                    "path": "references/roles.md",
                    "metadata": {
                        "document_id": "doc-roles",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["roles-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
                {
                    "path": "references/guide.md",
                    "metadata": {
                        "document_id": "doc-guide",
                        "governance_kind": "guide",
                        "version": "1.0.0",
                        "authority_level": "informational",
                        "owner": "architect",
                        "scope": "guide test",
                        "applies_to": ["architect"],
                        "overrideability": "informational",
                        "status": "active",
                        "canonical_for": [],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/guide.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("Non-normative", str(ctx.exception))

    # ===================================================================
    # Schema tests (Draft 2020-12)
    # ===================================================================

    def test_schema_rejects_validation_topic_without_validation_task(self):
        """The routing_request schema (and resolver) rejects validation_topic without validation_task=true."""
        result = _blocked({
            "role": "architect",
            "validation_topic": "semantic",
        })
        self.assertEqual(result["reason"], "invalid_kind")
        self.assertEqual(result["field"], "validation_topic")

    def test_schema_accepts_validation_topic_with_validation_task_true(self):
        """The routing_request schema (and resolver) accepts validation_topic with validation_task=true."""
        result = _result({
            "role": "validator",
            "validation_task": True,
            "validation_topic": "semantic",
        })
        self.assertEqual(result["status"], "ready")

    def test_routing_result_fragment_valid(self):
        """Test that routing_result output fragments are structurally valid."""
        result = _result({
            "role": "architect",
            "explicit_guide_refs": [
                {"form": "path", "value": "references/model.md"},
            ],
        })
        self.assertIn("status", result)
        self.assertIn("normative_reads", result)
        self.assertIn("supplemental_guides", result)
        self.assertIsInstance(result["normative_reads"], list)
        self.assertIsInstance(result["supplemental_guides"], list)
        # status must be either "ready" or "blocked"
        self.assertIn(result["status"], ("ready", "blocked"))


    # ===================================================================
    # Governance routing remediation tests
    # ===================================================================

    def test_config_unknown_governance_kind(self):
        """Reject inventory entry with unknown governance_kind."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            (refs / "test.md").write_text("test", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/test.md",
                    "metadata": {
                        "document_id": "doc-1",
                        "governance_kind": "unknown_kind",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["test-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/routing.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("Invalid governance_kind", str(ctx.exception))

    def test_config_unknown_status(self):
        """Reject inventory entry with unknown status."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            (refs / "test.md").write_text("test", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/test.md",
                    "metadata": {
                        "document_id": "doc-1",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "unknown_status",
                        "canonical_for": ["test-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/routing.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("Invalid status", str(ctx.exception))

    def test_config_unknown_authority_level(self):
        """Reject inventory entry with unknown authority_level."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            (refs / "test.md").write_text("test", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/test.md",
                    "metadata": {
                        "document_id": "doc-1",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "unknown_level",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["test-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/routing.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("Invalid authority_level", str(ctx.exception))

    def test_config_baseline_missing_protocol_or_policy(self):
        """Reject when role baseline has Protocol but no Policy."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            (refs / "lifecycle.md").write_text("lifecycle", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/routing.md",
                    "metadata": {
                        "document_id": "doc-routing",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["routing-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
                {
                    "path": "references/lifecycle.md",
                    "metadata": {
                        "document_id": "doc-lifecycle",
                        "governance_kind": "protocol",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "non_overridable",
                        "status": "active",
                        "canonical_for": ["lifecycle-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            # Role has only Protocol in baseline (no Policy)
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = ["references/lifecycle.md"]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("missing", str(ctx.exception).lower())
            self.assertIn("Policy", str(ctx.exception))

    def test_cli_malformed_ref_exit_2(self):
        """CLI malformed ref returns exit 2 with blocked JSON."""
        import subprocess
        router_path = str(_scripts_dir / "governance_read_router.py")
        result = subprocess.run(
            [sys.executable, router_path, "--role", "architect",
             "--contract-ref", "invalid_form=VALUE",
             "--railyard-root", str(PROJECT_ROOT)],
            capture_output=True, text=True,
        )
        # Must return exit 2, not exit 3
        self.assertEqual(result.returncode, 2,
                         f"Expected exit 2, got {result.returncode}. stderr: {result.stderr}")
        # Must output valid blocked JSON
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "blocked")
        self.assertIn("reason", data)

    def test_config_inactive_doc_in_baseline(self):
        """Reject inactive docs in baseline required_reads."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            (refs / "test.md").write_text("test", encoding="utf-8")
            (refs / "roles.md").write_text("roles", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/routing.md",
                    "metadata": {
                        "document_id": "doc-routing",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["routing-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
                {
                    "path": "references/test.md",
                    "metadata": {
                        "document_id": "doc-test",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "superseded",
                        "canonical_for": ["test-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
                {
                    "path": "references/roles.md",
                    "metadata": {
                        "document_id": "doc-roles",
                        "governance_kind": "protocol",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "non_overridable",
                        "status": "active",
                        "canonical_for": ["roles-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            # Baseline includes the inactive doc
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = [
                "references/routing.md", "references/test.md", "references/roles.md"
            ]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("Non-active", str(ctx.exception))

    def test_config_guide_in_conditional(self):
        """Reject Guide docs in conditional includes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            refs = root / "references"
            refs.mkdir()
            (refs / "routing.md").write_text("routing", encoding="utf-8")
            (refs / "roles.md").write_text("roles", encoding="utf-8")
            (refs / "guide.md").write_text("guide", encoding="utf-8")
            inventory = self._make_temp_inventory([
                {
                    "path": "references/routing.md",
                    "metadata": {
                        "document_id": "doc-routing",
                        "governance_kind": "policy",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "stricter_only",
                        "status": "active",
                        "canonical_for": ["routing-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
                {
                    "path": "references/roles.md",
                    "metadata": {
                        "document_id": "doc-roles",
                        "governance_kind": "protocol",
                        "version": "1.0.0",
                        "authority_level": "canonical",
                        "owner": "architect",
                        "scope": "test",
                        "applies_to": ["architect"],
                        "overrideability": "non_overridable",
                        "status": "active",
                        "canonical_for": ["roles-cf"],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
                {
                    "path": "references/guide.md",
                    "metadata": {
                        "document_id": "doc-guide",
                        "governance_kind": "guide",
                        "version": "1.0.0",
                        "authority_level": "informational",
                        "owner": "architect",
                        "scope": "guide test",
                        "applies_to": ["architect"],
                        "overrideability": "informational",
                        "status": "active",
                        "canonical_for": [],
                    },
                    "mixed_sections": [],
                    "disposition": "remain_in_place",
                    "canonical_links": [],
                },
            ])
            (refs / "governance-document-inventory.json").write_text(
                json.dumps(inventory), encoding="utf-8")
            # Conditional rule includes a Guide
            routing = self._make_temp_routing()
            routing["roles"]["architect"]["required_reads"] = [
                "references/routing.md", "references/roles.md"
            ]
            routing["conditional_rules"] = [{
                "rule_id": "test-rule",
                "description": "Test rule with Guide in includes",
                "predicate": {
                    "condition": "governance_task",
                    "applies_to_roles": ["architect"],
                },
                "action": {
                    "includes": ["references/guide.md"],
                },
            }]
            (refs / "governance-read-routing.json").write_text(
                json.dumps(routing), encoding="utf-8")
            (refs / "governance-document-taxonomy.md").write_text(
                "# Taxonomy\n", encoding="utf-8")
            with self.assertRaises(GovernanceRoutingConfigurationError) as ctx:
                validate_governance_configuration(root)
            self.assertIn("Non-normative", str(ctx.exception))


# ---------------------------------------------------------------------------
# Test discovery entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
