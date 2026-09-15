import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from jsonschema import Draft202012Validator

from runtime_gate_decision import evaluate_gate


def request(outcome="pass", gate_type="custom", classification="complete"):
    gate = {"gate_id": "gate", "gate_type": gate_type, "required": True, "failure_behavior": "halt_stage"}
    if gate_type == "validator": gate["contract_ref"] = {"artifact_id": "contract", "artifact_kind": "contract", "artifact_version": "1"}
    ref_name = {"validator": "report_ref", "artifact_shape": "artifact_ref", "diff_review": "diff_ref", "custom": "custom_source_ref"}[gate_type]
    signal = {ref_name: {"artifact_id": "report", "artifact_kind": "report", "artifact_version": "1"}, "overall_verdict" if gate_type == "validator" else "outcome": outcome}
    if outcome != "pass": signal["failure_code"] = {"fail": "validator_fail_deterministic", "blocked": "evidence_incomplete", "inconclusive": "evidence_absent_inconclusive", "human_review_required": "evidence_absent_human"}[outcome]
    envelope = {"envelope_id": "env", "gate_id": "gate", "primary_evidence": [{"artifact_id": "evidence", "artifact_kind": "evidence", "artifact_version": "1"}], "evidence_classification": classification, "collected_at": "2026-01-01T00:00:00Z", "collected_by": "collector"}
    if classification != "complete": envelope["missing_evidence_description"] = ["missing"]
    if gate_type == "validator": envelope["validation_report"] = copy.deepcopy(signal[ref_name])
    return {"request_kind": "initial", "decision_id": "decision", "evaluated_at": "2026-01-01T00:00:00Z", "evaluated_by": "runner", "gate_declaration": gate, "evidence_envelope": envelope, "evaluation_signal": signal, "run_context": {"run_id": "run", "stage_id": "stage"}, "execution_mode": "full"}


class GateDecisionTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.schema = Draft202012Validator(json.loads((cls.ROOT / "assets/schemas/runtime-gate-decision-v2.schema.json").read_text(encoding="utf-8")))

    def assert_root_schema(self, value):
        errors = list(self.schema.iter_errors(value))
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))
    def test_all_signal_outcomes(self):
        for outcome, classification in (("pass", "complete"), ("fail", "complete"), ("blocked", "partial_recoverable"), ("inconclusive", "partial_absent"), ("human_review_required", "partial_absent")):
            result = evaluate_gate(request(outcome, "custom", classification))
            self.assertEqual(result["outcome"], outcome)
            self.assert_root_schema(result)
    def test_validator_reference_mismatch(self):
        data = request("pass", "validator"); data["evidence_envelope"]["validation_report"]["artifact_id"] = "other"
        self.assertEqual(evaluate_gate(data)["error_code"], "report_reference_mismatch")
    def test_input_and_output_are_independent(self):
        data = request(); before = copy.deepcopy(data); result = evaluate_gate(data)
        self.assertEqual(data, before); result["evidence"][0]["artifact_id"] = "changed"; self.assertEqual(data, before)
    def test_reevaluation_and_override(self):
        data = request(); prior = evaluate_gate(data); again = request(); again.update({"request_kind": "reevaluation", "previous_decision_snapshot": prior, "prior_evidence_ids": ["evidence:evidence:1"]})
        self.assertEqual(evaluate_gate(again)["error_code"], "reevaluation_no_new_evidence")
        over = {"request_kind": "override", "decision_id": "two", "evaluated_at": "t", "evaluated_by": "x", "gate_declaration": {"gate_id": "gate", "gate_type": "custom", "required": False, "allow_gate_override": True, "failure_behavior": "halt_stage"}, "previous_decision": prior, "override_authorization": {"intervention_id": "i", "authorized_by": "human", "authorized_at": "t", "reason": "r"}, "run_context": {"run_id": "r", "stage_id": "s"}}
        self.assertEqual(evaluate_gate(over)["outcome"], "pass")

    def test_all_failure_codes_and_gate_types_are_schema_valid(self):
        cases = {
            "validator_fail_deterministic": ("fail", "complete"),
            "evidence_incomplete": ("blocked", "partial_recoverable"),
            "validator_unreachable": ("blocked", "partial_recoverable"),
            "validator_report_missing": ("blocked", "partial_recoverable"),
            "contract_unavailable": ("blocked", "partial_recoverable"),
            "required_gate_blocked": ("blocked", "partial_recoverable"),
            "degradation_blocked_correctness": ("blocked", "partial_recoverable"),
            "evidence_absent_inconclusive": ("inconclusive", "partial_absent"),
            "evidence_conflict_inconclusive": ("inconclusive", "conflicted"),
            "contract_insufficient_inconclusive": ("inconclusive", "partial_absent"),
            "unsupported_judgment_inconclusive": ("inconclusive", "conflicted"),
            "truth_hierarchy_inconclusive": ("inconclusive", "partial_absent"),
            "evidence_absent_human": ("human_review_required", "partial_absent"),
            "evidence_conflict_human": ("human_review_required", "conflicted"),
            "contract_insufficient_human": ("human_review_required", "partial_recoverable"),
            "unsupported_judgment_human": ("human_review_required", "complete"),
            "truth_hierarchy_human": ("human_review_required", "complete"),
        }
        for gate_type in ("validator", "artifact_shape", "diff_review", "custom"):
            for code, (outcome, classification) in cases.items():
                if gate_type in {"artifact_shape", "diff_review"} and outcome not in {"pass", "fail"}:
                    continue
                data = request(outcome, gate_type, classification)
                data["evaluation_signal"]["failure_code"] = code
                result = evaluate_gate(data)
                self.assertEqual(result.get("failure_code"), code, (gate_type, code))
                self.assert_root_schema(result)

    def test_recommendation_matrix(self):
        for behavior, recommendation in (("halt_stage", "stop_stage"), ("halt_run", "stop_run"), ("require_intervention", "human_intervention"), ("warn", "proceed_with_warning")):
            data = request("fail", "custom", "complete")
            data["gate_declaration"].update({"required": False, "allow_gate_override": False, "failure_behavior": behavior})
            self.assertEqual(evaluate_gate(data)["recommendation"], recommendation)
        for outcome, classification, recommendation in (("pass", "complete", "proceed"), ("blocked", "partial_recoverable", "more_evidence"), ("inconclusive", "partial_absent", "human_intervention"), ("human_review_required", "complete", "human_intervention")):
            data = request(outcome, "custom", classification)
            if outcome == "human_review_required": data["evaluation_signal"]["failure_code"] = "unsupported_judgment_human"
            self.assertEqual(evaluate_gate(data)["recommendation"], recommendation)

    def test_every_error_code_and_precedence(self):
        # The frozen taxonomy order is asserted with competing defects, not
        # merely isolated invalid inputs.
        cases = []
        bad = request(); bad.pop("decision_id"); cases.append((bad, "invalid_input_branch"))
        bad = request(); bad["gate_declaration"]["gate_type"] = "bogus"; cases.append((bad, "unknown_gate_type"))
        bad = request(); bad["gate_declaration"]["required"] = True; bad["gate_declaration"]["failure_behavior"] = "warn"; cases.append((bad, "required_gate_warn_behavior"))
        bad = request("pass", "validator"); bad["evidence_envelope"]["validation_report"]["artifact_id"] = "other"; cases.append((bad, "report_reference_mismatch"))
        bad = request(); bad["evidence_envelope"]["evidence_classification"] = "partial_absent"; bad["evidence_envelope"]["missing_evidence_description"] = ["x"]; cases.append((bad, "invalid_evidence_classification"))
        bad = request("pass", "validator"); bad["evaluation_signal"]["overall_verdict"] = "unknown"; cases.append((bad, "validator_verdict_mismatch"))
        bad = request("pass", "artifact_shape"); bad["evaluation_signal"]["outcome"] = "blocked"; bad["evaluation_signal"]["failure_code"] = "evidence_incomplete"; cases.append((bad, "outcome_not_in_enum"))
        bad = request("fail", "custom", "complete"); bad["gate_declaration"]["failure_behavior"] = "bogus"; cases.append((bad, "recommendation_not_in_matrix"))
        bad = request("fail", "custom", "complete"); bad["evaluation_signal"]["failure_code"] = "not-a-code"; cases.append((bad, "failure_code_ambiguous"))
        bad = request("fail", "custom", "complete"); bad["evaluation_signal"]["failure_code"] = "evidence_incomplete"; cases.append((bad, "failure_code_invalid"))
        base = request(); previous = evaluate_gate(base); bad = request(); bad.update({"request_kind": "reevaluation", "previous_decision_snapshot": previous, "prior_evidence_ids": ["evidence:evidence:1"]}); cases.append((bad, "reevaluation_no_new_evidence"))
        bad = {"request_kind": "override", "decision_id": "d", "evaluated_at": "t", "evaluated_by": "x", "gate_declaration": base["gate_declaration"], "previous_decision": previous, "override_authorization": {"intervention_id": "i", "authorized_by": "human", "authorized_at": "t", "reason": "r"}, "run_context": {"run_id": "r", "stage_id": "s"}}; cases.append((bad, "invalid_override"))
        self.assertEqual({expected for _, expected in cases}, {"invalid_input_branch", "unknown_gate_type", "invalid_override", "reevaluation_no_new_evidence", "required_gate_warn_behavior", "report_reference_mismatch", "invalid_evidence_classification", "validator_verdict_mismatch", "outcome_not_in_enum", "recommendation_not_in_matrix", "failure_code_ambiguous", "failure_code_invalid"})
        for data, expected in cases:
            result = evaluate_gate(data)
            self.assertEqual(result.get("error_code"), expected, expected)
            self.assert_root_schema(result)
        # Structural branch failure wins over unknown gate type; required-warn
        # wins before a report mismatch; reevaluation wins before later checks.
        conflict = request(); conflict.pop("decision_id"); conflict["gate_declaration"]["gate_type"] = "bogus"
        self.assertEqual(evaluate_gate(conflict)["error_code"], "invalid_input_branch")
        conflict = request("pass", "validator"); conflict["gate_declaration"]["failure_behavior"] = "warn"; conflict["evidence_envelope"]["validation_report"]["artifact_id"] = "other"
        self.assertEqual(evaluate_gate(conflict)["error_code"], "required_gate_warn_behavior")
        conflict = request(); prior = evaluate_gate(conflict); conflict.update({"request_kind": "reevaluation", "previous_decision_snapshot": prior, "prior_evidence_ids": ["evidence:evidence:1"]}); conflict["gate_declaration"]["failure_behavior"] = "warn"
        self.assertEqual(evaluate_gate(conflict)["error_code"], "reevaluation_no_new_evidence")

    def test_complete_semantic_precedence_pair_oracle(self):
        """Every realizable pair is covered; incompatible pairs are explicit."""
        ordered = (
            "invalid_override", "reevaluation_no_new_evidence",
            "required_gate_warn_behavior", "report_reference_mismatch",
            "invalid_evidence_classification", "validator_verdict_mismatch",
            "outcome_not_in_enum", "recommendation_not_in_matrix",
            "failure_code_ambiguous", "failure_code_invalid",
        )

        def apply(data, code):
            if code == "reevaluation_no_new_evidence":
                prior = evaluate_gate(request())
                prior_ids = ["evidence:evidence:1"]
                if data["gate_declaration"]["gate_type"] == "validator":
                    prior_ids.append("report:report:1")
                data.update({"request_kind": "reevaluation", "previous_decision_snapshot": prior,
                             "prior_evidence_ids": prior_ids})
            elif code == "required_gate_warn_behavior":
                data["gate_declaration"]["failure_behavior"] = "warn"
            elif code == "report_reference_mismatch":
                data["evidence_envelope"]["validation_report"]["artifact_id"] = "other"
            elif code == "invalid_evidence_classification":
                data["evidence_envelope"].update({"evidence_classification": "not-a-classification", "missing_evidence_description": ["x"]})
            elif code == "validator_verdict_mismatch":
                data["evaluation_signal"]["overall_verdict"] = "not-a-verdict"
            elif code == "outcome_not_in_enum":
                data["evaluation_signal"]["outcome"] = "not-an-outcome"
            elif code == "recommendation_not_in_matrix":
                data["gate_declaration"]["failure_behavior"] = "not-a-behavior"
            elif code == "failure_code_ambiguous":
                data["evaluation_signal"]["failure_code"] = "not-a-failure-code"
            elif code == "failure_code_invalid":
                data["evaluation_signal"]["failure_code"] = "evidence_incomplete"
            else:
                raise AssertionError(code)

        all_pairs = {(left, right) for index, left in enumerate(ordered) for right in ordered[index + 1:]}
        # Override has no evidence/signal semantic branch. Validator-only errors
        # cannot share a request with non-validator outcome validation, and a
        # single failure_code cannot simultaneously be missing/ambiguous and
        # admissible-but-invalid.
        impossible = {
            pair for pair in all_pairs if "invalid_override" in pair
        } | {
            ("report_reference_mismatch", "outcome_not_in_enum"),
            ("validator_verdict_mismatch", "outcome_not_in_enum"),
            ("required_gate_warn_behavior", "recommendation_not_in_matrix"),
            ("failure_code_ambiguous", "failure_code_invalid"),
        }
        realizable = all_pairs - impossible
        self.assertEqual(len(all_pairs), 45)
        self.assertEqual(len(realizable), 32)
        self.assertEqual(len(impossible), 13)

        for pair in sorted(realizable):
            validator_branch = bool({"report_reference_mismatch", "validator_verdict_mismatch"} & set(pair))
            data = request("fail", "validator" if validator_branch else "custom", "complete")
            for code in pair:
                apply(data, code)
            if "reevaluation_no_new_evidence" in pair:
                envelope = data["evidence_envelope"]
                refs = envelope["primary_evidence"] + envelope.get("supporting_evidence", [])
                if "validation_report" in envelope:
                    refs += [envelope["validation_report"]]
                data["prior_evidence_ids"] = [":".join(str(value) for value in (ref["artifact_id"], ref["artifact_kind"], ref.get("artifact_version"))) for ref in refs]
            expected = min(pair, key=ordered.index)
            self.assertEqual(evaluate_gate(data).get("error_code"), expected, pair)

        # Position 5 must beat position 6, including an invalid enum value.
        data = request("pass", "validator")
        apply(data, "report_reference_mismatch")
        apply(data, "invalid_evidence_classification")
        self.assertEqual(evaluate_gate(data)["error_code"], "report_reference_mismatch")
        # Position 6 must beat position 7 even when the verdict is malformed.
        data = request("fail", "validator")
        apply(data, "invalid_evidence_classification")
        apply(data, "validator_verdict_mismatch")
        self.assertEqual(evaluate_gate(data)["error_code"], "invalid_evidence_classification")

    def test_projection_reevaluation_and_override_variants(self):
        data = request(); ref = data["evidence_envelope"]["primary_evidence"][0]
        data["evidence_envelope"]["supporting_evidence"] = [copy.deepcopy(ref), {"artifact_id": "second", "artifact_kind": "evidence", "artifact_version": "1"}]
        result = evaluate_gate(data)
        self.assertEqual([item["artifact_id"] for item in result["evidence"]], ["evidence", "second"])
        self.assert_root_schema(result)
        replay = copy.deepcopy(data); replay["request_kind"] = "reevaluation"; replay["previous_decision_snapshot"] = result; replay["prior_evidence_ids"] = ["evidence:evidence:1", "second:evidence:1"]
        self.assertEqual(evaluate_gate(replay)["error_code"], "reevaluation_no_new_evidence")
        replay["evidence_envelope"]["primary_evidence"].append({"artifact_id": "new", "artifact_kind": "evidence", "artifact_version": "1"})
        self.assertEqual(evaluate_gate(replay)["previous_decision_id"], result["decision_id"])
        for key, value in (("authorized_by", "robot"), ("reason", "")):
            override = {"request_kind": "override", "decision_id": "o", "evaluated_at": "t", "evaluated_by": "x", "gate_declaration": {"gate_id": "gate", "gate_type": "custom", "required": False, "allow_gate_override": True, "failure_behavior": "halt_stage"}, "previous_decision": result, "override_authorization": {"intervention_id": "i", "authorized_by": "human", "authorized_at": "t", "reason": "r"}, "run_context": {"run_id": "r", "stage_id": "s"}}
            override["override_authorization"][key] = value
            self.assertEqual(evaluate_gate(override)["error_code"], "invalid_override")

    def test_validator_regression_contract_defects(self):
        missing_report = request("pass", "validator")
        missing_report["evidence_envelope"].pop("validation_report")
        self.assertEqual(evaluate_gate(missing_report)["error_code"], "invalid_input_branch")
        mismatch = request("pass", "validator")
        mismatch["evidence_envelope"]["validation_report"]["artifact_version"] = "2"
        self.assertEqual(evaluate_gate(mismatch)["error_code"], "report_reference_mismatch")

        prior = evaluate_gate(request())
        reevaluation = request()
        reevaluation.update({"request_kind": "reevaluation", "previous_decision_snapshot": {"decision_id": prior["decision_id"]}, "prior_evidence_ids": ["evidence:evidence:1"]})
        self.assertEqual(evaluate_gate(reevaluation)["error_code"], "invalid_input_branch")
        reevaluation["previous_decision_snapshot"] = prior
        reevaluation["evaluation_signal"] = {"custom_source_ref": {"artifact_id": "x", "artifact_kind": "custom_source"}, "outcome": "fail"}
        self.assertEqual(evaluate_gate(reevaluation)["error_code"], "invalid_input_branch")

        override = {"request_kind": "override", "decision_id": "o", "evaluated_at": "t", "evaluated_by": "x", "gate_declaration": {"gate_id": "gate", "gate_type": "custom", "required": False, "allow_gate_override": True, "failure_behavior": "halt_stage"}, "previous_decision": {"decision_id": "only-id"}, "override_authorization": {"intervention_id": "i", "authorized_by": "human", "authorized_at": "t", "reason": "r"}, "run_context": {"run_id": "r", "stage_id": "s"}}
        self.assertEqual(evaluate_gate(override)["error_code"], "invalid_override")

        invalid_class = request("pass", "validator")
        invalid_class["evaluation_signal"]["overall_verdict"] = "not-a-verdict"
        invalid_class["evidence_envelope"]["evidence_classification"] = "not-a-classification"
        invalid_class["evidence_envelope"]["missing_evidence_description"] = ["x"]
        self.assertEqual(evaluate_gate(invalid_class)["error_code"], "invalid_evidence_classification")

    def test_fresh_process_determinism(self):
        data = request("fail", "custom", "complete")
        encoded = json.dumps(data, separators=(",", ":"))
        program = "import json,sys;sys.path.insert(0,'scripts');from runtime_gate_decision import evaluate_gate;print(json.dumps(evaluate_gate(json.loads(sys.argv[1])),sort_keys=True,separators=(',',':')))"
        first = subprocess.check_output([sys.executable, "-c", program, encoded], cwd=self.ROOT, text=True).strip()
        second = subprocess.check_output([sys.executable, "-c", program, encoded], cwd=self.ROOT, text=True).strip()
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first), evaluate_gate(data))

    def test_frozen_catalog_and_request_fixtures(self):
        """All frozen fixtures are covered at their request/output-data layer."""
        catalog = json.loads((self.ROOT / "examples/runtime_gate_decision_contract/conformance.json").read_text(encoding="utf-8"))
        self.assertEqual(len(catalog), 76)
        for fixture in catalog:
            self.assertIn("expected_contract_valid", fixture)
            data = fixture["data"]
            self.assertEqual(self.schema.is_valid(data), fixture["expected_schema_valid"], fixture["fixture_id"])
            if "request_kind" not in data:
                continue  # GateDecision/Error fixtures exercise the output-schema layer.
            result = evaluate_gate(copy.deepcopy(data))
            expected = fixture.get("expected_error_code")
            if expected:
                self.assertEqual(result.get("error_code"), expected, fixture["fixture_id"])
            elif fixture["expected_contract_valid"]:
                self.assertNotIn("error_code", result, fixture["fixture_id"])

    def test_frozen_authorities_and_pure_source(self):
        authorities = {
            "references/runtime-gate-decision-contract.md": "711d1139b8c463024876f2460ff42bb195784dc7bc43d1d04bd2fc1c6d582033",
            "assets/schemas/runtime-gate-decision-v2.schema.json": "32cd278b25bd348cb9e810cec27337f72d9bfceef43f01c50c8bcddfc280264a",
            "examples/runtime_gate_decision_contract/conformance.json": "779ad6f792d84b4148525893a6e3bbb89db999f18625594433baae2b90ccfaa1",
        }
        for relative, prefix in authorities.items():
            self.assertTrue(hashlib.sha256((self.ROOT / relative).read_bytes()).hexdigest().startswith(prefix))
        source = (self.ROOT / "scripts/runtime_gate_decision.py").read_text(encoding="utf-8")
        self.assertNotIn("import ", source)
        for forbidden in ("open(", "sqlite", "os.", "time.", "random", "logging", "subprocess", "requests", "jsonschema", "workflow", "journal", "sidecar", "projection", "adapter"):
            self.assertNotIn(forbidden, source)

if __name__ == "__main__": unittest.main()
