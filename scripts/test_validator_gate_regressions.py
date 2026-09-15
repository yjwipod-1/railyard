#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
LIVE_DB = ROOT / ".workflow" / "workflow.db"
DISPOSABLE_ROOT = pathlib.Path(
    os.environ.get("RAILYARD_TEST_TEMP_ROOT", str(ROOT / ".tmp" / "validator-gate-tests"))
).resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import railyard_mcp_server
import ticket
import validate_artifacts
from workflow_schema import ensure_schema


def file_hash(path: pathlib.Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ValidatorGateRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.live_db_hash_before = file_hash(LIVE_DB)

    @classmethod
    def tearDownClass(cls) -> None:
        if file_hash(LIVE_DB) != cls.live_db_hash_before:
            raise AssertionError(f"source-local workflow DB hash changed during tests: {LIVE_DB}")

    def setUp(self) -> None:
        DISPOSABLE_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="railyard-validator-gate-", dir=DISPOSABLE_ROOT)
        self.temp_base = pathlib.Path(self.temp_dir.name).resolve()
        self.project_root = self.temp_base / "project"
        self.db_path = self.temp_base / "state" / "workflow.db"
        self.assertNotIn(".workflow", self.project_root.parts)
        self.assertNotIn(".workflow", self.db_path.parts)
        (self.project_root / "docs" / "system" / "inbox").mkdir(parents=True)
        (self.project_root / "docs" / "system" / "outbox").mkdir(parents=True)
        self.db_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            ensure_schema(conn)
            conn.commit()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        self.assertEqual(self.live_db_hash_before, file_hash(LIVE_DB))

    def ticket_path(self, ticket_id: str) -> pathlib.Path:
        return self.project_root / "docs" / "system" / "inbox" / f"{ticket_id}.md"

    def write_ticket(self, ticket_id: str, gate: str, complete: bool = True) -> pathlib.Path:
        gate_lines: list[str] = []
        if gate == "not_required":
            gate_lines = [
                "validator_required: false",
                "validator_gate_reason: Low-risk disposable lifecycle test.",
            ]
        elif gate == "required":
            gate_lines = [
                "validator_required: true",
                "validator_gate_reason: Required independent review gate.",
                "validator_risk_level: high",
                "validator_contract_source: ticket acceptance criteria",
                "validator_expected_artifacts: candidate.json",
                "validator_evidence_pack: source.json",
                "validator_failure_behavior: reject acceptance unless the independent Validator verdict is pass",
            ]
            if not complete:
                gate_lines = gate_lines[:-1]
        elif gate != "legacy":
            raise ValueError(f"unsupported gate fixture: {gate}")
        path = self.ticket_path(ticket_id)
        text = "\n".join(
            [
                "---",
                f"ticket_id: {ticket_id}",
                "epic_id: SYSTEM-FIXTURE-E001",
                "task_mode: general",
                "task_type: change",
                "priority: high",
                f"outbox_result_path: docs/system/outbox/{ticket_id}.result.json",
                *gate_lines,
                "---",
                "",
                f"# {ticket_id} - Disposable regression ticket",
                "",
                "## Task",
                "",
                "Exercise validator gate enforcement behavior.",
                "",
                "## Scope",
                "",
                "- Disposable regression state only.",
                "",
                "## Acceptance Checks",
                "",
                "- Expected lifecycle behavior is enforced.",
                "",
            ]
        )
        path.write_text(text, encoding="utf-8")
        return path

    def sync_ticket(self, ticket_id: str) -> dict[str, object]:
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            ticket.command_sync_mailbox(conn, self.project_root, "system", ticket_id)
            row = ticket.fetch_row(conn, "system", ticket_id)
        self.assertIsNotNone(row)
        return row or {}

    def write_runner_result(self, ticket_id: str) -> pathlib.Path:
        path = self.project_root / "docs" / "system" / "outbox" / f"{ticket_id}.result.json"
        payload = {
            "ticket_id": ticket_id,
            "runner_status": "done",
            "summary": "Disposable Runner result.",
            "files_changed": [],
            "validation": ["runner verification only"],
            "notes": [],
            "created_at": "2026-06-04T00:00:00+00:00",
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def prepare_review(self, ticket_id: str, gate: str = "required") -> None:
        self.write_ticket(ticket_id, gate)
        self.sync_ticket(ticket_id)
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            ticket.command_claim(conn, "system", ticket_id, "runner", "disposable-runner")
        result_path = self.write_runner_result(ticket_id)
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            ticket.command_mark_runner_result(
                conn,
                self.project_root,
                "system",
                ticket_id,
                "done",
                str(result_path),
            )

    def report_finding(self, verdict: str) -> dict[str, object]:
        status_by_verdict = {
            "pass": "pass",
            "fail": "fail",
            "blocked": "blocked",
            "inconclusive": "inconclusive",
            "human_review_required": "fail",
        }
        severity = "error" if verdict in {"pass", "fail", "blocked", "inconclusive"} else "info"
        return {
            "rule_id": f"fixture-{verdict}",
            "severity": severity,
            "status": status_by_verdict[verdict],
            "message": f"Disposable {verdict} finding.",
            "evidence": f"verdict={verdict}",
        }

    def write_validator_record(
        self,
        ticket_id: str,
        verdict: str,
        *,
        independence: str = "independent",
        hash_override: str | None = None,
    ) -> str:
        evidence_dir = self.project_root / "evidence"
        evidence_dir.mkdir(exist_ok=True)
        report_path = evidence_dir / f"{ticket_id}-{verdict}.validator-report.json"
        report = {
            "validator_role": "validator",
            "contract_id": "validator-gate-disposable",
            "contract_version": "1",
            "overall_verdict": verdict,
            "confidence": "high",
            "artifact_summary": {"candidate.json": {"kind": "candidate", "status": "read"}},
            "findings": [self.report_finding(verdict)],
            "missing_evidence": [],
            "recommended_next_action": "Return report to Architect.",
            "validated_artifacts": ["candidate.json"],
            "commands_run": [],
            "notes": None,
        }
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        record_path = evidence_dir / f"{ticket_id}-{verdict}.validator-record.json"
        record = {
            "record_type": ticket.VALIDATOR_REPORT_RECORD_TYPE,
            "ticket_id": ticket_id,
            "validator_role": "validator",
            "independence": independence,
            "producer_identity": "disposable-independent-validator",
            "report_path": str(report_path.relative_to(self.project_root)).replace("\\", "/"),
            "report_sha256": hash_override or ticket.sha256_file(report_path),
            "created_at": "2026-06-04T00:00:00+00:00",
        }
        record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return str(record_path.relative_to(self.project_root)).replace("\\", "/")

    def mark_review(
        self,
        ticket_id: str,
        review_result: str,
        validator_report_record: str | None = None,
    ) -> dict[str, object]:
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            return ticket.command_mark_review_result(
                conn,
                "system",
                ticket_id,
                review_result,
                None,
                project_root=self.project_root,
                validator_report_record_path=validator_report_record,
            )

    def test_legacy_ticket_is_readable_syncable_dispatchable_valid_and_not_inferred_false(self) -> None:
        ticket_id = "SYSTEM-LEGACY-001"
        path = self.write_ticket(ticket_id, "legacy")
        before = path.read_text(encoding="utf-8")
        validate_artifacts.validate_ticket(path)
        loaded = ticket.load_ticket_row(self.project_root, "system", path)
        self.assertEqual(ticket_id, loaded["ticket_id"])
        command = [
            sys.executable,
            str(SCRIPT_DIR / "architect.py"),
            "--lane",
            "system",
            "--db",
            str(self.db_path),
            "--project-root",
            str(self.project_root),
            "--runner-name",
            "disposable-runner",
            "dispatch-next-runner",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("ready", payload["status"])
        self.assertEqual("system", payload["lane"])
        self.assertIn(ticket_id, payload["synced"]["synced_ticket_ids"])
        self.assertEqual(ticket_id, payload["ticket"]["ticket_id"])
        self.assertEqual("ready", payload["ticket"]["status"])
        self.assertEqual("runner", payload["ticket"]["next_actor"])
        self.assertEqual("runner", payload["spawn"]["role"])
        self.assertEqual("disposable-runner", payload["spawn"]["runner_name"])
        self.assertEqual("railyard.runner_dispatch.v5", payload["spawn"]["contract"])
        self.assertIn("governance_route_request", payload["spawn"])
        self.assertEqual("runner", payload["spawn"]["governance_route_request"]["role"])
        self.assertIn("governance_route_result", payload["spawn"])
        self.assertEqual("ready", payload["spawn"]["governance_route_result"]["status"])
        self.assertIn("normative_reads", payload["spawn"]["governance_route_result"])
        # Verify 5-file Runner baseline
        reads = payload["spawn"]["governance_route_result"]["normative_reads"]
        self.assertIn("SKILL.md", reads)
        self.assertIn("references/roles.md", reads)
        self.assertIn("references/startup-sequence.md", reads)
        self.assertIn("references/ticket-format.md", reads)
        self.assertIn("references/result-format.md", reads)
        gate = ticket.load_ticket_validator_gate(self.project_root, payload["ticket"], ticket_id)
        self.assertEqual("legacy_missing", gate["state"])
        self.assertIsNone(gate["validator_required"])
        self.assertEqual(before, path.read_text(encoding="utf-8"))
        self.assertNotIn("validator_required", path.read_text(encoding="utf-8"))

    def test_legacy_and_explicit_no_gate_tickets_can_complete_review_without_false_inference(self) -> None:
        for suffix, gate in (("LEGACY", "legacy"), ("NO-GATE", "not_required")):
            with self.subTest(gate=gate):
                ticket_id = f"SYSTEM-{suffix}-ACCEPT"
                self.prepare_review(ticket_id, gate)
                row = self.mark_review(ticket_id, "accept")
                self.assertEqual("finalised", row["status"])
                with closing(sqlite3.connect(str(self.db_path))) as conn:
                    event = ticket.command_events(conn, "system", ticket_id, 1)[0]
                expected_state = "legacy_missing" if gate == "legacy" else "not_required"
                self.assertEqual(expected_state, event["payload"]["validator_gate"]["state"])
                self.assertIsNone(event["payload"]["validator_report"])

    def test_new_draft_requires_explicit_gate_decision(self) -> None:
        command = [
            sys.executable,
            str(SCRIPT_DIR / "ticket.py"),
            "--lane",
            "system",
            "--db",
            str(self.db_path),
            "--project-root",
            str(self.project_root),
            "draft",
            "--ticket-id",
            "SYSTEM-NEW-DRAFT",
            "--epic-id",
            "SYSTEM-FIXTURE-E001",
            "--title",
            "Missing gate",
            "--task",
            "Must be rejected.",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("--validator-required", completed.stderr)
        self.assertFalse(self.ticket_path("SYSTEM-NEW-DRAFT").exists())

    def test_explicit_required_gate_requires_complete_metadata(self) -> None:
        path = self.write_ticket("SYSTEM-INCOMPLETE-GATE", "required", complete=False)
        with self.assertRaisesRegex(ValueError, "missing gate metadata"):
            ticket.load_ticket_row(self.project_root, "system", path)
        with self.assertRaisesRegex(validate_artifacts.ValidationError, "missing gate metadata"):
            validate_artifacts.validate_ticket(path)

    def test_required_gate_rejects_acceptance_without_report_for_both_accept_results(self) -> None:
        for index, review_result in enumerate(("accept", "accept_with_changes"), start=1):
            with self.subTest(review_result=review_result):
                ticket_id = f"SYSTEM-NO-REPORT-{index}"
                self.prepare_review(ticket_id)
                with self.assertRaisesRegex(RuntimeError, "requires independent Validator evidence"):
                    self.mark_review(ticket_id, review_result)
                with closing(sqlite3.connect(str(self.db_path))) as conn:
                    row = ticket.fetch_row(conn, "system", ticket_id)
                self.assertEqual("awaiting_review", row["status"])

    def test_required_gate_accepts_only_pass_verdict(self) -> None:
        for index, review_result in enumerate(("accept", "accept_with_changes"), start=1):
            with self.subTest(review_result=review_result):
                ticket_id = f"SYSTEM-PASS-{index}"
                self.prepare_review(ticket_id)
                record = self.write_validator_record(ticket_id, "pass")
                row = self.mark_review(ticket_id, review_result, record)
                self.assertEqual("finalised", row["status"])

    def test_required_gate_rejects_all_non_passing_verdicts(self) -> None:
        for index, verdict in enumerate(("fail", "blocked", "inconclusive", "human_review_required"), start=1):
            with self.subTest(verdict=verdict):
                ticket_id = f"SYSTEM-VERDICT-{index}"
                self.prepare_review(ticket_id)
                record = self.write_validator_record(ticket_id, verdict)
                with self.assertRaisesRegex(RuntimeError, "does not permit accept"):
                    self.mark_review(ticket_id, "accept", record)

    def test_required_gate_still_allows_reject_and_redesign_without_report(self) -> None:
        for index, review_result in enumerate(("reject", "redesign"), start=1):
            with self.subTest(review_result=review_result):
                ticket_id = f"SYSTEM-RETURN-{index}"
                self.prepare_review(ticket_id)
                row = self.mark_review(ticket_id, review_result)
                expected = "ready" if review_result == "reject" else "drafted"
                self.assertEqual(expected, row["status"])

    def test_artifact_shape_runner_verification_and_architect_self_review_are_not_validator_reports(self) -> None:
        excluded_records: list[tuple[str, str]] = []

        artifact_ticket = "SYSTEM-EXCLUDED-ARTIFACT"
        self.prepare_review(artifact_ticket)
        artifact_path = self.project_root / "artifact-shape-output.json"
        artifact_path.write_text(
            json.dumps({"status": "ok", "validation_kind": "artifact_shape", "independent_validator_evidence": False}),
            encoding="utf-8",
        )
        excluded_records.append((artifact_ticket, str(artifact_path.relative_to(self.project_root))))

        runner_ticket = "SYSTEM-EXCLUDED-RUNNER"
        self.prepare_review(runner_ticket)
        runner_path = self.project_root / "docs" / "system" / "outbox" / f"{runner_ticket}.result.json"
        excluded_records.append((runner_ticket, str(runner_path.relative_to(self.project_root))))

        architect_ticket = "SYSTEM-EXCLUDED-ARCHITECT"
        self.prepare_review(architect_ticket)
        architect_record = self.write_validator_record(architect_ticket, "pass", independence="role_collapsed")
        excluded_records.append((architect_ticket, architect_record))

        for ticket_id, record in excluded_records:
            with self.subTest(ticket_id=ticket_id):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.mark_review(ticket_id, "accept", record)

    def test_validator_report_reference_hash_must_verify(self) -> None:
        ticket_id = "SYSTEM-HASH-MISMATCH"
        self.prepare_review(ticket_id)
        record = self.write_validator_record(ticket_id, "pass", hash_override="0" * 64)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.mark_review(ticket_id, "accept", record)

    def test_mcp_lite_mark_review_result_uses_shared_enforcement_path(self) -> None:
        ticket_id = "SYSTEM-MCP-GATE"
        self.prepare_review(ticket_id)
        config = railyard_mcp_server.ServerConfig(db_path=self.db_path, project_root=self.project_root)
        with self.assertRaisesRegex(RuntimeError, "requires independent Validator evidence"):
            railyard_mcp_server.apply_mark_review_result(config, "system", ticket_id, "accept")
        record = self.write_validator_record(ticket_id, "pass")
        result = railyard_mcp_server.apply_mark_review_result(
            config,
            "system",
            ticket_id,
            "accept",
            validator_report_record=record,
        )
        self.assertEqual("finalised", result["ticket"]["status"])

    def test_governance_typed_flags_affect_dispatch_route(self) -> None:
        """Runner dispatch with runtime_task should include runtime-architecture in route."""
        # Write a ticket for ready dispatch
        ticket_id = "SYSTEM-GOV-001"
        self.write_ticket(ticket_id, "legacy")

        # Dispatch with runtime_task flag
        command = [
            sys.executable, str(SCRIPT_DIR / "architect.py"),
            "--lane", "system", "--db", str(self.db_path),
            "--project-root", str(self.project_root),
            "--runner-name", "gov-runner",
            "--runtime-task",
            "dispatch-next-runner",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("ready", payload["status"])
        route_result = payload["spawn"]["governance_route_result"]
        self.assertIn("references/runtime-architecture.md", route_result["normative_reads"])

    def test_blocked_governance_route_returns_no_prompt(self) -> None:
        """Dispatch with malformed contract ref should return blocked status, no prompt."""
        ticket_id = "SYSTEM-GOV-002"
        self.write_ticket(ticket_id, "legacy")

        command = [
            sys.executable, str(SCRIPT_DIR / "architect.py"),
            "--lane", "system", "--db", str(self.db_path),
            "--project-root", str(self.project_root),
            "--runner-name", "gov-blocked",
            "--contract-ref", "invalid_form=VALUE",
            "dispatch-next-runner",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        payload = json.loads(completed.stdout)
        # The spawn should be a blocker, not a prompt
        self.assertEqual("blocked", payload["spawn"]["status"])
        self.assertNotIn("prompt", payload["spawn"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
