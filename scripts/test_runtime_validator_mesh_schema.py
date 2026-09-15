"""Authority-derived Runtime Validator Mesh v1.2 schema harness."""

import copy
import hashlib
import json
import os
import re
import unittest
from collections import Counter

from jsonschema import Draft202012Validator


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "assets", "schemas", "runtime-validator-mesh-v1.schema.json")
CATALOG_PATH = os.path.join(ROOT, "examples", "runtime_validator_mesh_contract", "conformance.json")
CONTRACT_PATH = os.path.join(ROOT, "references", "runtime-validator-mesh-contract.md")
VALIDATOR_PROTOCOL_PATH = os.path.join(ROOT, "references", "validator-protocol.md")
CONTRACT_SHA256 = "efe7689f1c258200137f4e02f037d18a24a01063c1fe24a9f5948086da869e68"


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def codes(text):
    return re.findall(r"`([^`]+)`", text)


def unique(values):
    return list(dict.fromkeys(values))


def authority():
    """Extract public versions, enums, matrix rows, and counts from authority."""
    text = load_text(CONTRACT_PATH)
    protocol = load_text(VALIDATOR_PROTOCOL_PATH)
    version = re.search(r"^version:\s*([^\s]+)$", text, re.MULTILINE).group(1)
    roots = codes(re.search(r"public roots remain (.+?)\. Existing", text).group(1))
    dispatch = codes(re.search(r"Canonical dispatch statuses are (.+?)\. The last", text).group(1))
    kinds = codes(re.search(r"`requirement_kind` is exactly (.+?); `required`", text).group(1))
    policies = codes(re.search(r"declares `missing_mapping_policy` as exactly (.+?)\. An extension", text).group(1))
    freshness_block = re.search(r"\| Status \| Exact predicate \| Deterministic details \|(.+?)\n\nFor a contract", text, re.DOTALL).group(1)
    freshness = unique(re.findall(r"^\| `([^`]+)` \|", freshness_block, re.MULTILINE))
    error_block = re.search(r"\| Order \| Error code \| Predicate \|(.+?)\n\nThe v1.0", text, re.DOTALL).group(1)
    errors = re.findall(r"\| \d+ \| `([^`]+)` \|", error_block)
    aggregate = [value.strip() for value in re.search(r"strict order:\s*\n\n```\n(.+?)\n```", text, re.DOTALL).group(1).split(">")]
    actions = dict(re.findall(r"`(pass|fail|blocked|inconclusive|human_review_required)\s*->\s*([a-z_]+)`", text))
    matrix_block = re.search(r"\| Requirement \| Dispatch/freshness condition \| Requirement result \| Contribution \|(.+?)\n\nAfter applying", text, re.DOTALL).group(1)
    matrix_rows = []
    for line in matrix_block.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        matrix_rows.append(cells)
    result_kinds = unique(codes(row[2])[0] for row in matrix_rows)
    confidence = codes(re.search(r"`confidence` MUST be one of (.+?);", protocol).group(1))
    report_confidence = codes(re.search(r"`report_confidence` \(exactly (.+?)\)", text).group(1))
    report_verdicts = list(reversed(aggregate))
    recommended_actions = unique(actions[verdict] for verdict in report_verdicts)

    # These are inherited v1.0 public-shape enums under Section 2's explicit
    # shape-preservation clause. They are not inferred from the candidate catalog.
    failure_behaviors = ["halt_mesh", "halt_run", "require_intervention"]
    risk_levels = confidence
    enum_values = {
        "requirement_kind": kinds,
        "failure_behavior": failure_behaviors,
        "missing_mapping_policy": policies,
        "risk_level": risk_levels,
        "dispatch_status": dispatch,
        "report_overall_verdict": report_verdicts,
        "aggregate_verdict": aggregate,
        "aggregate_confidence": confidence,
        "freshness_status": freshness,
        "result_kind": result_kinds,
        "recommended_action": recommended_actions,
        "report_confidence": report_confidence,
        "error_code": errors,
    }
    counts = {
        "root_public_types": len(roots),
        "error_codes": len(errors),
        "dispatch_statuses": len(dispatch),
        "freshness_statuses": len(freshness),
        "result_kinds": len(result_kinds),
        "report_verdicts": len(report_verdicts),
        "aggregate_verdicts": len(aggregate),
        "requirement_kinds": len(kinds),
        "missing_mapping_policies": len(policies),
        "recommended_actions": len(recommended_actions),
        "confidence_levels": len(confidence),
        "risk_levels": len(risk_levels),
        "failure_behaviors": len(failure_behaviors),
    }
    return {
        "version": version,
        "roots": roots,
        "enums": enum_values,
        "counts": counts,
        "actions": actions,
        "errors": errors,
        "matrix_rows": matrix_rows,
    }


def root_validator(validator, schema, root_type):
    return validator.evolve(schema=schema["$defs"][root_type])


def validate_entry(validator, schema, entry):
    return list(root_validator(validator, schema, entry["root_type"]).iter_errors(entry["data"]))


def artifact_key(ref):
    return tuple(ref[key] for key in ("artifact_id", "artifact_kind", "artifact_version", "digest"))


def expected_group_status(member, members):
    ordered = sorted(members, key=lambda item: (item["dispatch_priority"], item["declaration_order"], item["requirement_id"]))
    first = ordered[0]
    conflict = any(item["report_digest"] != first["report_digest"] or item["report_verdict"] != first["report_verdict"] for item in ordered[1:])
    index = ordered.index(member)
    if index > 0 and member["report_digest"] == first["report_digest"]:
        return "duplicate"
    if conflict:
        return "conflicting"
    return "current"


def matrix_selection(requirement_kind, required, missing_policy, dispatch_status, report_verdict, freshness):
    if dispatch_status == "report_produced":
        if freshness == "current":
            return "report", report_verdict, None
        if required:
            return "unusable_required_report", "blocked", None
        return "optional_excluded", None, "optional_unusable_report"
    if requirement_kind == "baseline":
        return "missing_baseline", missing_policy, None
    if required:
        return "missing_required_extension", "blocked", None
    return "optional_excluded", None, "optional_unavailable"


def enumerate_matrix(auth):
    requirements = [
        ("baseline", required, policy)
        for required in (True, False)
        for policy in auth["enums"]["missing_mapping_policy"]
    ] + [("extension", required, None) for required in (True, False)]
    cells = []
    unavailable = [status for status in auth["enums"]["dispatch_status"] if status != "report_produced"]
    for kind, required, policy in requirements:
        for status in unavailable:
            cells.append((kind, required, policy, status, None, None, matrix_selection(kind, required, policy, status, None, None)))
        for verdict in auth["enums"]["report_overall_verdict"]:
            cells.append((kind, required, policy, "report_produced", verdict, "current", matrix_selection(kind, required, policy, "report_produced", verdict, "current")))
            for freshness in auth["enums"]["freshness_status"]:
                if freshness != "current":
                    cells.append((kind, required, policy, "report_produced", verdict, freshness, matrix_selection(kind, required, policy, "report_produced", verdict, freshness)))
    return cells


def semantic_violations(entry):
    """Return every independently detected contract violation for one fixture."""
    data = entry["data"]
    context = entry.get("semantic_context", {})
    failures = set()
    if entry["root_type"] == "ValidationReportBinding" and data["report_ref"]["digest"] is not None and data["report_sha256"] is not None and data["report_ref"]["digest"] != data["report_sha256"]:
        failures.add("report_digest_equals_sha")
    if entry["root_type"] == "ValidatorMeshDeclaration":
        ids = [item["requirement_id"] for item in data["requirements"]]
        if len(ids) != len(set(ids)):
            failures.add("requirement_ids_unique")
    if entry["root_type"] == "ValidatorMeshEvaluationRequest":
        requirement = data["mesh_declaration"]["requirements"][0]
        binding = data["dispatch_results"][0].get("report_binding")
        if binding and artifact_key(binding["contract_ref"]) != artifact_key(requirement["contract_ref"]):
            failures.add("binding_contract_key_equals_requirement")
    if entry["root_type"] == "ValidatorMeshResult":
        contributions = [row["verdict_contribution"] for row in data["requirement_results"] if "verdict_contribution" in row]
        precedence = ["pass", "inconclusive", "human_review_required", "blocked", "fail"]
        if not contributions and data["aggregate_verdict"] != "inconclusive":
            failures.add("zero_contribution_verdict")
        if contributions:
            expected = precedence[max(precedence.index(value) for value in contributions)]
            if data["aggregate_verdict"] != expected:
                failures.add("aggregate_precedence")
        action_map = authority()["actions"]
        if data["recommended_action"] != action_map[data["aggregate_verdict"]]:
            failures.add("recommended_action_mapping")
    if entry["root_type"] == "FreshnessAssessment" and data["freshness_status"] == "current" and data["freshness_details"] != {}:
        failures.add("freshness_detail_matches_status")
    if "declared_target_key" in context and tuple(context["declared_target_key"]) != tuple(context["binding_target_key"]):
        if data["freshness_status"] != "mismatched" or data["freshness_details"] != {"field_category": "target_artifact_binding"}:
            failures.add("target_key_equality")
    if entry.get("semantic_probe") in ("duplicate_group_ordering", "conflict_group_ordering"):
        member = next(item for item in context["members"] if item["binding_id"] == data["binding_id"])
        if data["freshness_status"] != expected_group_status(member, context["members"]):
            failures.add(entry["semantic_probe"])
    if entry.get("semantic_probe") == "contribution_matrix_row":
        row = data["requirement_results"][0]
        expected_kind, expected_contribution, expected_exclusion = matrix_selection(
            row["requirement_kind"], row["required"], context["missing_mapping_policy"],
            row["dispatch_status"], row["report_verdict_or_null"], row["freshness_or_null"])
        actual = (row["result_kind"], row.get("verdict_contribution"), row.get("excluded_reason"))
        if actual != (expected_kind, expected_contribution, expected_exclusion):
            failures.add("contribution_matrix_row")
    if entry.get("semantic_probe") == "freshness_predicate_precedence":
        ordered = [
            ("superseded", context["superseded"], {"supersession": {}}),
            ("invalidated", context["invalidated"], {"invalidation": {}}),
            ("stale", context["report_identity_missing"], {"field_category": "report_identity"}),
            ("mismatched", context["contract_mismatch"], {"field_category": "contract_binding"}),
            ("mismatched", context["target_mismatch"], {"field_category": "target_artifact_binding"}),
        ]
        expected_status, expected_details = next(((status, details) for status, applies, details in ordered if applies), ("current", {}))
        if (data["freshness_status"], data["freshness_details"]) != (expected_status, expected_details):
            failures.add("freshness_predicate_precedence")
    if entry.get("semantic_probe") == "error_first_match_precedence":
        error_order = authority()["errors"]
        expected = min(context["matching_errors"], key=error_order.index)
        if data["error_code"] != expected:
            failures.add("error_first_match_precedence")
    return failures


def repaired_structural_fixture(entry, catalog):
    data = copy.deepcopy(entry["data"])
    fixture_id = entry["id"]
    if fixture_id == "declaration-v10":
        data["mesh_version"] = "1.2.0"
    elif fixture_id == "requirement-incomplete-contract":
        data["contract_ref"]["digest"] = "b" * 64
    elif fixture_id == "requirement-noncontract-kind":
        data["contract_ref"]["artifact_kind"] = "contract"
    elif fixture_id == "requirement-multi-target":
        data["artifact_scope"] = data["artifact_scope"][:1]
    elif fixture_id == "baseline-without-policy":
        data["missing_mapping_policy"] = "fail"
    elif fixture_id == "extension-with-policy":
        data.pop("missing_mapping_policy")
    elif fixture_id == "produced-without-binding":
        data["report_binding"] = copy.deepcopy(next(item["data"] for item in catalog["positives"] if item["id"] == "binding-pass"))
    elif fixture_id == "unreachable-with-binding":
        data.pop("report_binding")
    elif fixture_id == "degraded-without-note":
        data["degradation_note"] = "storage unavailable"
    elif fixture_id == "binding-report-digest-shape":
        data["report_sha256"] = data["report_ref"]["digest"]
    elif fixture_id == "binding-role-collapse":
        data["independent_production_evidence"]["no_caller_role_collapse"] = True
    elif fixture_id == "result-mixed-contribution":
        data["requirement_results"][0].pop("verdict_contribution")
    elif fixture_id == "freshness-unknown":
        data["freshness_status"] = "current"
    elif fixture_id == "freshness-conflict-details":
        data["freshness_details"]["requirement_ids"].append("second")
    elif fixture_id == "error-deprecated-aggregate":
        data["error_code"] = "invalid_report_binding"
    elif fixture_id == "binding-no-confidence":
        data["report_confidence"] = "high"
    elif fixture_id == "binding-stale-empty-artifact-id":
        data["report_ref"]["artifact_id"] = "report-a"
    elif fixture_id == "binding-stale-empty-artifact-version":
        data["report_ref"]["artifact_version"] = "0.7.0"
    elif fixture_id == "binding-stale-empty-digest":
        data["report_ref"]["digest"] = "d" * 64
    elif fixture_id == "binding-invalid-confidence":
        data["report_confidence"] = "high"
    elif fixture_id == "binding-wrong-report-kind":
        data["report_ref"]["artifact_kind"] = "validation_report"
    elif fixture_id == "binding-empty-sha256":
        data["report_sha256"] = "d" * 64
    else:
        raise AssertionError("missing structural repair for " + fixture_id)
    return data


class AuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load_json(SCHEMA_PATH)
        cls.catalog = load_json(CATALOG_PATH)
        cls.authority = authority()

    def test_contract_binding_and_schema_identity(self):
        self.assertEqual(digest(CONTRACT_PATH), CONTRACT_SHA256)
        self.assertEqual(self.schema["$id"], "urn:railyard:schema:runtime-validator-mesh:v" + self.authority["version"])
        self.assertEqual(self.catalog["contract"]["contract_version"], self.authority["version"])

    def test_all_public_roots_are_authority_derived(self):
        self.assertEqual(self.catalog["counts"]["root_public_types"], len(self.authority["roots"]))
        for root in self.authority["roots"]:
            self.assertIn(root, self.schema["$defs"])

    def test_all_public_enums_and_counts_are_authority_derived(self):
        defs = self.schema["$defs"]
        schema_enums = {
            "requirement_kind": defs["ValidatorRequirement"]["properties"]["requirement_kind"]["enum"],
            "failure_behavior": defs["ValidatorRequirement"]["properties"]["failure_behavior"]["enum"],
            "missing_mapping_policy": defs["ValidatorRequirement"]["properties"]["missing_mapping_policy"]["enum"],
            "risk_level": defs["ValidatorDispatchRequest"]["properties"]["risk_level"]["enum"],
            "dispatch_status": defs["ValidatorDispatchResult"]["properties"]["dispatch_status"]["enum"],
            "report_overall_verdict": defs["ValidationReportBinding"]["properties"]["report_overall_verdict"]["enum"],
            "aggregate_verdict": defs["Contribution"]["enum"],
            "aggregate_confidence": defs["ValidatorMeshResult"]["properties"]["aggregate_confidence"]["enum"],
            "freshness_status": defs["FreshnessAssessment"]["properties"]["freshness_status"]["enum"],
            "result_kind": defs["RequirementResult"]["properties"]["result_kind"]["enum"],
            "recommended_action": defs["ValidatorMeshResult"]["properties"]["recommended_action"]["enum"],
            "report_confidence": defs["ValidationReportBinding"]["properties"]["report_confidence"]["enum"],
            "error_code": defs["ValidatorMeshEvaluationError"]["properties"]["error_code"]["enum"],
        }
        for name, expected in self.authority["enums"].items():
            self.assertEqual(set(schema_enums[name]), set(expected), name + " schema")
            self.assertEqual(set(self.catalog["enums"][name]), set(expected), name + " catalog")
        self.assertEqual(self.catalog["counts"], self.authority["counts"])

    def test_complete_identity_and_deprecated_error_boundary(self):
        defs = self.schema["$defs"]
        self.assertEqual(defs["ArtifactRef"]["required"], ["artifact_id", "artifact_kind", "artifact_version", "digest"])
        active = set(self.authority["enums"]["error_code"])
        self.assertTrue(active.isdisjoint({"zero_valid_contributions", "required_requirement_blocked", "baseline_missing_unresolved"}))

    def test_all_property_objects_are_closed(self):
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object" and "properties" in node:
                    self.assertIs(node.get("additionalProperties"), False)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(self.schema)


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)
        cls.catalog = load_json(CATALOG_PATH)
        cls.authority = authority()

    def test_check_schema(self):
        Draft202012Validator.check_schema(self.schema)

    def test_every_positive_is_valid_and_request_mapping_is_correct(self):
        roots = set()
        covered = {name: set() for name in self.authority["enums"]}

        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in covered and not isinstance(item, (dict, list)):
                        covered[key].add(item)
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        for entry in self.catalog["positives"]:
            roots.add(entry["root_type"])
            self.assertEqual(validate_entry(self.validator, self.schema, entry), [], entry["id"])
            collect(entry["data"])
            if entry["root_type"] == "ValidatorMeshEvaluationRequest":
                requirement_ids = {item["requirement_id"] for item in entry["data"]["mesh_declaration"]["requirements"]}
                dispatch_ids = [item["dispatch_request_id"] for item in entry["data"]["dispatch_results"]]
                self.assertEqual(set(dispatch_ids), requirement_ids, entry["id"] + " mapping")
                self.assertEqual(len(dispatch_ids), len(set(dispatch_ids)), entry["id"] + " duplicate dispatch")
        self.assertTrue(set(self.authority["roots"]).issubset(roots))
        for name, expected in self.authority["enums"].items():
            self.assertTrue(set(expected).issubset(covered[name]), name + " positive coverage")

    def test_every_structural_negative_is_isolated_by_repair(self):
        for entry in self.catalog["schema_invalid"]:
            self.assertTrue(entry.get("cause"), entry["id"])
            self.assertTrue(validate_entry(self.validator, self.schema, entry), entry["id"] + " unexpectedly valid")
            repaired = copy.deepcopy(entry)
            repaired["data"] = repaired_structural_fixture(entry, self.catalog)
            self.assertEqual(validate_entry(self.validator, self.schema, repaired), [], entry["id"] + " has a second structural cause")

    def test_every_semantic_negative_is_schema_valid_and_isolated(self):
        for entry in self.catalog["schema_valid_contract_invalid"]:
            self.assertEqual(validate_entry(self.validator, self.schema, entry), [], entry["id"] + " schema-invalid")
            self.assertEqual(semantic_violations(entry), {entry["semantic_probe"]}, entry["id"] + " semantic isolation")


class MatrixTests(unittest.TestCase):
    def test_complete_234_cell_matrix(self):
        cells = enumerate_matrix(authority())
        self.assertEqual(len(cells), 234)
        counts = Counter(cell[-1][0] for cell in cells)
        self.assertEqual(counts, Counter({
            "report": 30,
            "missing_baseline": 16,
            "missing_required_extension": 4,
            "unusable_required_report": 90,
            "optional_excluded": 94,
        }))
        for cell in cells:
            kind, required, policy, status, verdict, freshness, selected = cell
            self.assertEqual(selected, matrix_selection(kind, required, policy, status, verdict, freshness))

    def test_contract_matrix_rows_match_derived_result_kinds(self):
        auth = authority()
        self.assertEqual(len(auth["matrix_rows"]), 6)
        self.assertEqual(set(auth["enums"]["result_kind"]), {"report", "missing_baseline", "missing_required_extension", "unusable_required_report", "optional_excluded"})


class AuthorityDriftTests(unittest.TestCase):
    """Regression assertions distinguishing active requirements from version history."""

    @classmethod
    def setUpClass(cls):
        cls.text = load_text(CONTRACT_PATH)

    def _active_text(self):
        """Extract contract prose before the version history section."""
        match = re.search(r"^(.*?)(?=\n## 8\. Version History)", self.text, re.DOTALL)
        self.assertIsNotNone(match, "version history section not found in contract")
        return match.group(1)

    def _history_text(self):
        """Extract version history section text."""
        match = re.search(r"## 8\. Version History\n\n(.*?)(?=\n## 9\.)", self.text, re.DOTALL)
        self.assertIsNotNone(match, "version history section not found in contract")
        return match.group(1)

    def test_no_prohibited_active_phrases(self):
        """Prohibited v1.0/v1.1 current-authority phrases must not appear in active prose."""
        active = self._active_text()
        prohibited = ["v1.1 requirement", "v1.0.0 requirement", "v1.1 ArtifactRef"]
        for phrase in prohibited:
            self.assertNotIn(phrase, active,
                f"prohibited active phrase '{phrase}' found in current-authority prose")

    def test_active_prose_consistently_names_120(self):
        """Current-authority prose must consistently name v1.2.0."""
        active = self._active_text()
        self.assertIn("v1.2.0", active,
            "active prose does not reference version v1.2.0")

    def test_three_row_version_history_preserved(self):
        """Version history must contain exactly three rows with technical content."""
        history = self._history_text()
        rows = [line for line in history.splitlines()
                if line.startswith("| 1.") and "---" not in line]
        self.assertEqual(len(rows), 3,
            f"version history must contain exactly 3 rows, found {len(rows)}")
        for i, row in enumerate(rows):
            self.assertIn("| 1.", row,
                f"version history row {i} missing version prefix")

    def test_no_system_identifiers_anywhere(self):
        """Contract must have zero SYSTEM-* identifiers in public text."""
        self.assertNotRegex(self.text, r'SYSTEM-\d+',
            "contract contains SYSTEM-* identifiers")

    def test_public_hygiene(self):
        """Contract must have zero private domains, non-ASCII chars, Agent brands,
        Control paths, or local paths."""
        # Zero non-ASCII characters
        self.assertTrue(all(ord(c) < 128 for c in self.text),
            "contract contains non-ASCII characters")
        # Zero Agent brands
        agent_brands = ["CodeBuddy", "Claude", "Copilot", "Cursor", "Windsurf", "WorkBuddy"]
        for brand in agent_brands:
            self.assertNotIn(brand, self.text,
                f"contract contains Agent brand '{brand}'")
        # Zero Control paths
        self.assertNotRegex(self.text, r'Railyard-Control',
            "contract contains Control paths")
        # Zero local filesystem paths
        self.assertNotRegex(self.text, r'[A-Za-z]:[\\\\/]',
            "contract contains local filesystem paths")
        # Zero private domains
        self.assertNotRegex(self.text, r'\.local\b',
            "contract contains private domain references")


if __name__ == "__main__":
    unittest.main(verbosity=2)
