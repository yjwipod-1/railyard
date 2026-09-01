"""Adversarial mutation coverage for the frozen Knowledge Contract validator."""

import copy
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from validate_artifacts import ValidationError, validate_knowledge_fixture  # noqa: E402


FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "examples" / "knowledge_contract_fixtures"
FIXTURE_NAMES = [
    "fixture-valid-knowledge-entry.json",
    "fixture-ineligible-entry.json",
    "fixture-missing-provenance.json",
    "fixture-supersession-chain.json",
    "fixture-broken-supersession.json",
    "fixture-multi-ticket-aggregation.json",
    "fixture-ticket-multi-functionality.json",
]


def _load(name: str = "fixture-valid-knowledge-entry.json") -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class KnowledgeMutationTests(unittest.TestCase):
    def assert_mutation_rule(self, mutate, rule: str, fixture: str = "fixture-valid-knowledge-entry.json"):
        data = copy.deepcopy(_load(fixture))
        data["expected_outcome"] = "pass"
        data.pop("expected_failure_rule", None)
        mutate(data)
        with self.assertRaises(ValidationError) as context:
            validate_knowledge_fixture(data, pathlib.Path("mutation.json"))
        self.assertIn(rule, str(context.exception))

    def test_missing_visibility(self):
        self.assert_mutation_rule(lambda data: data["entries"][0].pop("visibility"), "visibility-required")

    def test_invalid_visibility(self):
        self.assert_mutation_rule(lambda data: data["entries"][0].__setitem__("visibility", "internal"), "visibility-enum")

    def test_non_scalar_visibility_is_deterministic(self):
        self.assert_mutation_rule(lambda data: data["entries"][0].__setitem__("visibility", {}), "visibility-enum")

    def test_malformed_artifact_ref(self):
        self.assert_mutation_rule(lambda data: data["entries"][0]["evidence"][0].pop("artifact_kind"), "artifact-ref-shape")

    def test_unresolved_artifact_ref(self):
        self.assert_mutation_rule(lambda data: data["entries"][0]["evidence"][0].__setitem__("artifact_id", "absent-report"), "artifact-ref-resolution")

    def test_artifact_ref_digest_must_match_resolved_inventory(self):
        def mutate(data):
            report = next(item for item in data["artifact_inventory"] if item["artifact_id"] == "validator-report")
            report["digest"] = "sha256:aaaa"
            data["entries"][0]["evidence"][0]["digest"] = "sha256:bbbb"
        self.assert_mutation_rule(mutate, "artifact-ref-supplement-mismatch")

    def test_artifact_ref_locator_must_match_resolved_inventory(self):
        def mutate(data):
            report = next(item for item in data["artifact_inventory"] if item["artifact_id"] == "validator-report")
            report["locator"] = "reports/canonical.json"
            data["entries"][0]["evidence"][0]["locator"] = "reports/different.json"
        self.assert_mutation_rule(mutate, "artifact-ref-supplement-mismatch")

    def test_scalar_provenance(self):
        self.assert_mutation_rule(lambda data: data["entries"][0].__setitem__("provenance", "accepted-source"), "provenance-shape")

    def test_string_evidence(self):
        self.assert_mutation_rule(lambda data: data["entries"][0].__setitem__("evidence", ["validator-report"]), "artifact-ref-shape")

    def test_relationship_branch_collision(self):
        def mutate(data):
            relationship = data["entries"][1]["relationships"][0]
            relationship["target_artifact"] = data["entries"][0]["evidence"][0]
        self.assert_mutation_rule(mutate, "relationship-branch-collision")

    def test_non_scalar_relationship_target_is_deterministic(self):
        self.assert_mutation_rule(lambda data: data["entries"][1]["relationships"][0].__setitem__("target_entry_id", []), "relationship-branch-collision")

    def test_missing_hierarchy_parent(self):
        self.assert_mutation_rule(lambda data: data["entries"][1].__setitem__("relationships", []), "hierarchy-parent-cardinality")

    def test_multiple_hierarchy_parents(self):
        def mutate(data):
            data["entries"][3]["relationships"].append(
                {"target_kind": "knowledge_entry", "target_entry_id": "knowledge-feature", "relationship": "part_of"}
            )
        self.assert_mutation_rule(mutate, "hierarchy-parent-cardinality")

    def test_duplicate_entry_identity(self):
        self.assert_mutation_rule(lambda data: data["entries"][1].__setitem__("entry_id", data["entries"][0]["entry_id"]), "duplicate-entry-identity")

    def test_duplicate_event_identity(self):
        self.assert_mutation_rule(lambda data: data["lifecycle_events"][1].__setitem__("event_id", data["lifecycle_events"][0]["event_id"]), "duplicate-event-identity")

    def test_dependency_cycle(self):
        def mutate(data):
            data["entries"][0]["relationships"].append(
                {"target_kind": "knowledge_entry", "target_entry_id": "knowledge-behavior", "relationship": "depends_on"}
            )
            data["entries"][3]["relationships"].append(
                {"target_kind": "knowledge_entry", "target_entry_id": "knowledge-domain", "relationship": "depends_on"}
            )
        self.assert_mutation_rule(mutate, "depends-on-cycle")

    def test_malformed_causation_xor(self):
        self.assert_mutation_rule(lambda data: data["lifecycle_events"][1].__setitem__("causation_chain", ["knowledge-event-review"]), "lifecycle-causation-xor")

    def test_duplicate_event_order(self):
        self.assert_mutation_rule(lambda data: data["lifecycle_events"][1].__setitem__("event_order", 1), "lifecycle-event-order")

    def test_non_integer_event_order_is_deterministic(self):
        self.assert_mutation_rule(lambda data: data["lifecycle_events"][1].__setitem__("event_order", "2"), "lifecycle-event-order")

    def test_replay_transition_mismatch(self):
        self.assert_mutation_rule(lambda data: data["lifecycle_events"][1]["prior_state"].__setitem__("confidence", "low"), "lifecycle-replay-transition")

    def test_legacy_confidence_field(self):
        self.assert_mutation_rule(lambda data: data["entries"][0].__setitem__("confidence", "high"), "legacy-entry-field")

    def test_all_legacy_supersession_fields(self):
        for field, value in (
            ("supersedes", "knowledge-domain"),
            ("superseded_by", "knowledge-domain"),
            ("valid_until", "0.9.0"),
            ("functionality_relationships", []),
        ):
            with self.subTest(field=field):
                self.assert_mutation_rule(lambda data, f=field, v=value: data["entries"][0].__setitem__(f, v), "legacy-entry-field")

    def test_incorrect_expected_failure_rule(self):
        data = _load("fixture-broken-supersession.json")
        data["expected_failure_rule"] = "visibility-enum"
        with self.assertRaises(ValidationError) as context:
            validate_knowledge_fixture(data, pathlib.Path("mutation.json"))
        self.assertIn("lifecycle-supersession-target", str(context.exception))

    def test_all_seven_fixtures_are_self_consistent(self):
        for name in FIXTURE_NAMES:
            with self.subTest(fixture=name):
                validate_knowledge_fixture(_load(name), FIXTURE_DIR / name)

    def test_visibility_coverage(self):
        observed = {
            entry["visibility"]
            for name in FIXTURE_NAMES
            for entry in _load(name)["entries"]
            if entry.get("visibility") in {"public", "project", "restricted"}
        }
        self.assertEqual(observed, {"public", "project", "restricted"})


if __name__ == "__main__":
    unittest.main()
