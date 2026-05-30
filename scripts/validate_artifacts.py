#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]

VALID_LANES = {"domain", "system"}
VALID_PRIORITIES = {"high", "medium", "low"}
VALID_EPIC_STATUSES = {"queued", "in_progress", "partial", "blocked", "done", "superseded"}
VALID_RUNNER_STATUSES = {"done", "partial", "blocked", "invalid"}
VALID_OVERALL_VERDICTS = {"pass", "fail", "blocked", "inconclusive", "human_review_required"}
VALID_FINDING_STATUSES = {"pass", "fail", "not_applicable", "blocked", "inconclusive"}
VALID_FINDING_SEVERITIES = {"error", "warn", "info"}
VALID_BLOCKER_CATEGORIES = {
    "permission_denied",
    "command_failed",
    "sandbox_boundary",
    "authorization_required",
    "environment_issue",
    "unresolved_dependency",
}
VALID_VALIDATION_SCOPES = {"extract_only", "transform_only", "ingest_to_db"}
VALID_MISSING_MAPPING_POLICIES = {"inconclusive", "fail", "human_review_required"}
FIELD_MAPPING_ROOT_FIELDS = {
    "contract_id", "version", "applies_to", "validation_scope",
    "field_mappings", "derived_field", "source_path",
}
FIELD_MAPPING_OBJECT_FIELDS = {"source_path", "derived_path", "transform"}
FIELD_MAPPING_FIXTURE_FIELDS = {
    "fixture_id", "description", "source_artifact",
    "derived_artifact", "field_mapping_contract", "expected_validation",
}

TICKET_REQUIRED_FIELDS = {"ticket_id", "epic_id", "task_mode", "task_type", "priority"}
TICKET_REQUIRED_SECTIONS = ("Task", "Scope", "Acceptance Checks")
EPIC_REQUIRED_FIELDS = {"epic_id", "lane", "status", "priority"}
EPIC_REQUIRED_SECTIONS = ("Summary", "Done Definition")
RESULT_REQUIRED_FIELDS = {
    "ticket_id",
    "runner_status",
    "summary",
    "files_changed",
    "validation",
    "notes",
    "created_at",
}
QUEUE_ITEM_REQUIRED_FIELDS = {"epic_id", "title", "status", "priority", "summary", "done_definition"}


class ValidationError(ValueError):
    pass


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc


def extract_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if not match:
        raise ValidationError("missing YAML-style frontmatter block")
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValidationError(f"invalid frontmatter line: {line!r}")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def missing_fields(values: dict[str, Any], required: set[str]) -> list[str]:
    return sorted(key for key in required if key not in values or values[key] in ("", None))


def require_sections(text: str, section_names: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for section_name in section_names:
        if not re.search(rf"^##\s+{re.escape(section_name)}\s*$", text, re.MULTILINE):
            missing.append(section_name)
    return missing


def relative(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def validate_ticket(path: pathlib.Path) -> None:
    text = read_text(path)
    frontmatter = extract_frontmatter(text)
    missing = missing_fields(frontmatter, TICKET_REQUIRED_FIELDS)
    if missing:
        raise ValidationError(f"missing required frontmatter fields: {', '.join(missing)}")
    ticket_id = frontmatter["ticket_id"]
    if path.stem != ticket_id:
        raise ValidationError(f"filename stem must match ticket_id {ticket_id!r}")
    if not re.search(rf"^#\s+{re.escape(ticket_id)}\s+-\s+\S", text, re.MULTILINE):
        raise ValidationError(f"missing title heading '# {ticket_id} - <Title>'")
    section_missing = require_sections(text, TICKET_REQUIRED_SECTIONS)
    if section_missing:
        raise ValidationError(f"missing required sections: {', '.join(section_missing)}")
    priority = frontmatter["priority"].lower()
    if priority not in VALID_PRIORITIES:
        raise ValidationError(f"invalid priority {priority!r}")


def validate_epic(path: pathlib.Path) -> None:
    text = read_text(path)
    frontmatter = extract_frontmatter(text)
    missing = missing_fields(frontmatter, EPIC_REQUIRED_FIELDS)
    if missing:
        raise ValidationError(f"missing required frontmatter fields: {', '.join(missing)}")
    epic_id = frontmatter["epic_id"]
    if path.stem != epic_id:
        raise ValidationError(f"filename stem must match epic_id {epic_id!r}")
    if frontmatter["lane"] not in VALID_LANES:
        raise ValidationError(f"invalid lane {frontmatter['lane']!r}")
    status = frontmatter["status"].lower()
    if status not in VALID_EPIC_STATUSES:
        raise ValidationError(f"invalid status {status!r}")
    priority = frontmatter["priority"].lower()
    if priority not in VALID_PRIORITIES:
        raise ValidationError(f"invalid priority {priority!r}")
    if not re.search(rf"^#\s+{re.escape(epic_id)}\s+-\s+\S", text, re.MULTILINE):
        raise ValidationError(f"missing title heading '# {epic_id} - <Title>'")
    section_missing = require_sections(text, EPIC_REQUIRED_SECTIONS)
    if section_missing:
        raise ValidationError(f"missing required sections: {', '.join(section_missing)}")


def validate_report(path: pathlib.Path) -> None:
    """Validate a Validation Report artifact against the V0.7 report schema."""
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValidationError("validation report JSON must be an object")
    required = {"contract_id", "contract_version", "results"}
    missing = missing_fields(payload, required)
    if missing:
        raise ValidationError(f"validation report missing required fields: {', '.join(missing)}")
    for key in ("contract_id", "contract_version"):
        require_string(payload, key)
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise ValidationError("validation report results must be a non-empty array")
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            raise ValidationError(f"results[{index}] must be an object")
        for field in ("artifact_path", "artifact_kind"):
            if field not in result or not isinstance(result[field], str) or not result[field].strip():
                raise ValidationError(f"results[{index}].{field} must be a non-empty string")
        if "overall_verdict" not in result:
            raise ValidationError(f"results[{index}].overall_verdict is required")
        if "findings" not in result:
            raise ValidationError(f"results[{index}].findings is required")
        overall = result.get("overall_verdict")
        if overall not in VALID_OVERALL_VERDICTS:
            raise ValidationError(
                f"results[{index}].overall_verdict must be one of {sorted(VALID_OVERALL_VERDICTS)}, got {overall!r}"
            )
        findings = result.get("findings")
        if not isinstance(findings, list) or not findings:
            raise ValidationError(f"results[{index}].findings must be a non-empty array")
        # Check for duplicate finding rule_ids within this result
        finding_rule_ids = [f.get("rule_id") for f in findings if isinstance(f, dict) and "rule_id" in f]
        if len(finding_rule_ids) != len(set(finding_rule_ids)):
            raise ValidationError(
                f"results[{index}].findings contains duplicate rule_id values"
            )
        for fidx, finding in enumerate(findings):
            if not isinstance(finding, dict):
                raise ValidationError(f"results[{index}].findings[{fidx}] must be an object")
            for ff in ("rule_id", "severity", "status", "message"):
                require_string(finding, ff)
            if finding["severity"] not in VALID_FINDING_SEVERITIES:
                raise ValidationError(
                    f"results[{index}].findings[{fidx}].severity must be one of {sorted(VALID_FINDING_SEVERITIES)}, got {finding['severity']!r}"
                )
            if finding["status"] not in VALID_FINDING_STATUSES:
                raise ValidationError(
                    f"results[{index}].findings[{fidx}].status must be one of {sorted(VALID_FINDING_STATUSES)}, got {finding['status']!r}"
                )
        # Internal consistency checks for overall_verdict vs findings
        validate_report_consistency(result, index)


def validate_report_consistency(result: dict[str, Any], index: int) -> None:
    """Validate internal consistency between overall_verdict and findings."""
    overall = result.get("overall_verdict")
    findings = result.get("findings", [])

    has_fail_severity = any(
        f.get("severity") == "error" and f.get("status") == "fail" for f in findings
    )
    has_blocked = any(f.get("status") == "blocked" for f in findings)
    has_inconclusive = any(f.get("status") == "inconclusive" for f in findings)
    has_error_fail = any(
        f.get("severity") == "error" and f.get("status") == "fail" for f in findings
    )
    has_pass_or_not_applicable = any(
        f.get("status") in ("pass", "not_applicable") for f in findings
    )

    if overall == "pass":
        if has_error_fail or has_blocked or has_inconclusive:
            raise ValidationError(
                f"results[{index}].overall_verdict=pass but found findings with status fail/blocked/inconclusive"
            )
    elif overall == "fail":
        if not has_fail_severity:
            raise ValidationError(
                f"results[{index}].overall_verdict=fail requires at least one severity=error AND status=fail finding"
            )
    elif overall == "blocked":
        if not has_blocked:
            raise ValidationError(
                f"results[{index}].overall_verdict=blocked requires at least one status=blocked finding"
            )
    elif overall == "inconclusive":
        if not has_inconclusive:
            raise ValidationError(
                f"results[{index}].overall_verdict=inconclusive requires at least one status=inconclusive finding"
            )
    elif overall == "human_review_required":
        review_reason = result.get("review_reason", "")
        if not isinstance(review_reason, str) or not review_reason.strip():
            raise ValidationError(
                f"results[{index}].overall_verdict=human_review_required requires non-empty review_reason"
            )


def validate_contract(path: pathlib.Path) -> None:
    """Validate a Validation Contract artifact against the V0.7 contract schema."""
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValidationError("validation contract JSON must be an object")
    required = {"contract_id", "version", "description", "applies_to", "rules"}
    missing = missing_fields(payload, required)
    if missing:
        raise ValidationError(f"validation contract missing required fields: {', '.join(missing)}")
    for key in ("contract_id", "version", "description"):
        require_string(payload, key)
    applies_to = payload.get("applies_to")
    if not isinstance(applies_to, list) or not applies_to:
        raise ValidationError("applies_to must be a non-empty array")
    if not all(isinstance(item, str) and item.strip() for item in applies_to):
        raise ValidationError("applies_to must be an array of non-empty strings")
    rules = payload.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValidationError("validation contract rules must be a non-empty array")
    for ridx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValidationError(f"rules[{ridx}] must be an object")
        rule_required = {"rule_id", "description", "severity", "check"}
        rule_missing = missing_fields(rule, rule_required)
        if rule_missing:
            raise ValidationError(
                f"rules[{ridx}] missing required fields: {', '.join(rule_missing)}"
            )
        require_string(rule, "rule_id")
        require_string(rule, "description")
        if rule["severity"] not in VALID_FINDING_SEVERITIES:
            raise ValidationError(
                f"rules[{ridx}].severity must be one of {sorted(VALID_FINDING_SEVERITIES)}, got {rule['severity']!r}"
            )
        check = rule.get("check")
        if not isinstance(check, dict):
            raise ValidationError(f"rules[{ridx}].check must be an object")
        if "type" not in check or not isinstance(check["type"], str) or not check["type"].strip():
            raise ValidationError(
                f"rules[{ridx}].check.type must be a non-empty string"
            )
    # Check for duplicate rule_id values across rules
    rule_ids = [rule.get("rule_id") for rule in rules if isinstance(rule, dict) and "rule_id" in rule]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValidationError(
            "validation contract rules contain duplicate rule_id values"
        )


def validate_result(path: pathlib.Path) -> None:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValidationError("result JSON must be an object")
    missing = missing_fields(payload, RESULT_REQUIRED_FIELDS)
    if missing:
        raise ValidationError(f"missing required fields: {', '.join(missing)}")
    runner_status = payload["runner_status"]
    if runner_status not in VALID_RUNNER_STATUSES:
        raise ValidationError(f"invalid runner_status {runner_status!r}")
    for key in ("files_changed", "validation", "notes"):
        if not isinstance(payload[key], list):
            raise ValidationError(f"{key} must be an array")
    for key in ("ticket_id", "summary", "created_at"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ValidationError(f"{key} must be a non-empty string")
    if "protocol_reads" in payload:
        protocol_reads = payload["protocol_reads"]
        if not isinstance(protocol_reads, list) or not protocol_reads:
            raise ValidationError("protocol_reads must be a non-empty array")
        if not all(isinstance(item, str) and item.strip() for item in protocol_reads):
            raise ValidationError("protocol_reads must be an array of non-empty strings")
    if "evidence" in payload:
        evidence = payload["evidence"]
        if not isinstance(evidence, list) or not all(isinstance(item, str) and item.strip() for item in evidence):
            raise ValidationError("evidence must be an array of non-empty strings")
    if "confidence" in payload and payload["confidence"] not in {"high", "medium", "low"}:
        raise ValidationError("confidence must be one of: high, medium, low")
    if "runner_trace" in payload:
        validate_runner_trace(payload["runner_trace"], runner_status)
    expected_ticket_id = path.name.removesuffix(".result.json")
    if payload["ticket_id"] != expected_ticket_id:
        raise ValidationError(f"ticket_id must match result filename {expected_ticket_id!r}")


def require_optional_string_or_null(payload: dict[str, Any], key: str, prefix: str) -> None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValidationError(f"{prefix}.{key} must be a non-empty string or null")


def validate_runner_trace(trace: Any, runner_status: str) -> None:
    if not isinstance(trace, dict):
        raise ValidationError("runner_trace must be an object")
    required = {"platform_name", "agent_profile", "attempts", "commands", "blocker_category", "next_action"}
    missing = sorted(required - set(trace))
    if missing:
        raise ValidationError(f"runner_trace missing required fields: {', '.join(missing)}")
    require_optional_string_or_null(trace, "platform_name", "runner_trace")
    require_optional_string_or_null(trace, "agent_profile", "runner_trace")
    attempts = trace.get("attempts")
    if not isinstance(attempts, int) or attempts < 1:
        raise ValidationError("runner_trace.attempts must be an integer >= 1")
    commands = trace.get("commands")
    if not isinstance(commands, list) or not all(isinstance(item, str) and item.strip() for item in commands):
        raise ValidationError("runner_trace.commands must be an array of non-empty command strings")
    blocker_category = trace.get("blocker_category")
    if runner_status == "blocked":
        if blocker_category not in VALID_BLOCKER_CATEGORIES:
            raise ValidationError(f"runner_trace.blocker_category must be one of {sorted(VALID_BLOCKER_CATEGORIES)} when runner_status is blocked")
    elif blocker_category is not None:
        raise ValidationError("runner_trace.blocker_category must be null unless runner_status is blocked")
    next_action = trace.get("next_action")
    if runner_status in {"blocked", "partial"}:
        if not isinstance(next_action, str) or not next_action.strip():
            raise ValidationError("runner_trace.next_action must be a non-empty string when runner_status is blocked or partial")
    elif next_action is not None and (not isinstance(next_action, str) or not next_action.strip()):
        raise ValidationError("runner_trace.next_action must be a non-empty string or null")


def require_string(payload: dict[str, Any], key: str) -> None:
    if not isinstance(payload.get(key), str) or not payload[key].strip():
        raise ValidationError(f"{key} must be a non-empty string")


def require_string_list(payload: dict[str, Any], key: str) -> None:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValidationError(f"{key} must be an array of non-empty strings")


def validate_queue(path: pathlib.Path) -> None:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValidationError("queue JSON must be an object")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationError("items must be a non-empty array")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(f"items[{index}] must be an object")
        missing = missing_fields(item, QUEUE_ITEM_REQUIRED_FIELDS)
        if missing:
            raise ValidationError(f"items[{index}] missing required fields: {', '.join(missing)}")
        for key in ("epic_id", "title", "status", "priority", "summary"):
            require_string(item, key)
        if item["status"] not in VALID_EPIC_STATUSES:
            raise ValidationError(f"items[{index}].status is invalid: {item['status']!r}")
        if item["priority"] not in VALID_PRIORITIES:
            raise ValidationError(f"items[{index}].priority is invalid: {item['priority']!r}")
        for key in ("done_definition", "preferred_entrypoints", "notes"):
            if key in item:
                require_string_list(item, key)


def validate_field_mapping_contract_shape(data: dict[str, Any]) -> None:
    """Validate a field mapping contract JSON against the v0.7 shape definition."""
    if not isinstance(data, dict):
        raise ValidationError("field mapping contract must be an object")

    missing = missing_fields(data, FIELD_MAPPING_ROOT_FIELDS)
    if missing:
        raise ValidationError(f"field mapping contract missing required fields: {', '.join(missing)}")

    # Validate applies_to is a valid string
    applies_to = data.get("applies_to")
    if not isinstance(applies_to, str) or not applies_to.strip():
        raise ValidationError("applies_to must be a non-empty string")

    # Validate validation_scope enum
    validation_scope = data.get("validation_scope")
    if validation_scope not in VALID_VALIDATION_SCOPES:
        raise ValidationError(
            f"validation_scope must be one of {sorted(VALID_VALIDATION_SCOPES)}, got {validation_scope!r}"
        )

    # Validate missing_mapping_policy enum (optional, default is inconclusive)
    missing_policy = data.get("missing_mapping_policy")
    if missing_policy is not None and missing_policy not in VALID_MISSING_MAPPING_POLICIES:
        raise ValidationError(
            f"missing_mapping_policy must be one of {sorted(VALID_MISSING_MAPPING_POLICIES)}, got {missing_policy!r}"
        )

    # Validate warnings_as_errors boolean (optional, default is false)
    warnings_as_errors = data.get("warnings_as_errors")
    if warnings_as_errors is not None and not isinstance(warnings_as_errors, bool):
        raise ValidationError("warnings_as_errors must be a boolean or null")

    # Validate field_mappings array
    field_mappings = data.get("field_mappings")
    if not isinstance(field_mappings, list) or not field_mappings:
        raise ValidationError("field_mappings must be a non-empty array")

    for fidx, mapping in enumerate(field_mappings):
        if not isinstance(mapping, dict):
            raise ValidationError(f"field_mappings[{fidx}] must be an object")
        missing = missing_fields(mapping, FIELD_MAPPING_OBJECT_FIELDS)
        if missing:
            raise ValidationError(
                f"field_mappings[{fidx}] missing required fields: {', '.join(missing)}"
            )
        require_string(mapping, "transform")
        require_string(mapping, "source_path")
        require_string(mapping, "derived_path")
        # validate optional fields
        if "required" in mapping and not isinstance(mapping["required"], bool):
            raise ValidationError(f"field_mappings[{fidx}].required must be a boolean")
        if "preserve_sign" in mapping and not isinstance(mapping["preserve_sign"], bool):
            raise ValidationError(f"field_mappings[{fidx}].preserve_sign must be a boolean")

    # Validate derived_field object
    derived_field = data.get("derived_field")
    if not isinstance(derived_field, dict):
        raise ValidationError("derived_field must be an object")
    require_string(derived_field, "derived_path")
    # source_path and expected_transform are optional for unmapped derived fields
    # (e.g., missing_mapping_policy=fail scenario where no source exists)


def validate_field_mapping_fixture(data: dict[str, Any]) -> None:
    """Validate a field mapping contract fixture against the v0.7 fixture schema."""
    if not isinstance(data, dict):
        raise ValidationError("fixture must be an object")

    missing = missing_fields(data, FIELD_MAPPING_FIXTURE_FIELDS)
    if missing:
        raise ValidationError(f"fixture missing required fields: {', '.join(missing)}")

    # Validate source_artifact
    source_artifact = data.get("source_artifact")
    if not isinstance(source_artifact, dict):
        raise ValidationError("source_artifact must be an object")
    if "artifact_kind" not in source_artifact or source_artifact.get("artifact_kind") != "source":
        raise ValidationError("source_artifact.artifact_kind must be 'source'")

    # Validate derived_artifact
    derived_artifact = data.get("derived_artifact")
    if not isinstance(derived_artifact, dict):
        raise ValidationError("derived_artifact must be an object")
    if "artifact_kind" not in derived_artifact or derived_artifact.get("artifact_kind") != "derived":
        raise ValidationError("derived_artifact.artifact_kind must be 'derived'")

    # Validate field_mapping_contract shape
    contract = data.get("field_mapping_contract")
    if not isinstance(contract, dict):
        raise ValidationError("field_mapping_contract must be an object")
    validate_field_mapping_contract_shape(contract)

    # Validate expected_validation
    expected_validation = data.get("expected_validation")
    if not isinstance(expected_validation, dict):
        raise ValidationError("expected_validation must be an object")
    if "overall_verdict" not in expected_validation:
        raise ValidationError("expected_validation must contain overall_verdict")
    verdict = expected_validation["overall_verdict"]
    if verdict not in VALID_OVERALL_VERDICTS:
        raise ValidationError(
            f"expected_validation.overall_verdict must be one of {sorted(VALID_OVERALL_VERDICTS)}, got {verdict!r}"
        )


def validate_example_validator_input_func(data: dict[str, Any]) -> None:
    """Validate a Validator usage example input JSON for structural integrity.

    Ensures the input references real artifacts (source/derived/contract) and
    does not incorrectly reference its own validation-report.json output as an
    input artifact.
    """
    if not isinstance(data, dict):
        raise ValidationError("example validator input must be an object")

    # artifacts must be a non-empty list of objects with path and kind
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValidationError("artifacts must be a non-empty array")
    for idx, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise ValidationError(f"artifacts[{idx}] must be an object")
        if "path" not in artifact or not isinstance(artifact["path"], str) or not artifact["path"].strip():
            raise ValidationError(f"artifacts[{idx}].path must be a non-empty string")
        if "kind" not in artifact or not isinstance(artifact["kind"], str) or not artifact["kind"].strip():
            raise ValidationError(f"artifacts[{idx}].kind must be a non-empty string")
        # Sanity: artifact path should end in .json
        path = artifact["path"]
        if not path.endswith(".json"):
            raise ValidationError(f"artifacts[{idx}].path must end with .json, got {path!r}")

    # validation_contract must be an object (even if minimal)
    validation_contract = data.get("validation_contract")
    if not isinstance(validation_contract, dict):
        raise ValidationError("validation_contract must be an object")

    # Sanity check: artifacts must NOT reference validation-report.json as
    # source or derived. This catches the common mistake of treating the
    # validator input/output pair as source/derived.
    for artifact in artifacts:
        artifact_path = artifact["path"]
        artifact_kind = artifact["kind"]
        basename = pathlib.Path(artifact_path).name
        if basename == "validation-report.json" and artifact_kind in ("source", "derived"):
            raise ValidationError(
                f"artifacts entry with path={artifact_path!r} has kind={artifact_kind!r}; "
                "validation-report.json must not be used as source or derived artifact "
                "(it is the Validator output, not the input data)"
            )

    # Evidence pack is optional but should be an object if present
    evidence_pack = data.get("evidence_pack")
    if evidence_pack is not None and not isinstance(evidence_pack, dict):
        raise ValidationError("evidence_pack must be an object or null")


def validate_example_validator_report_func(data: dict[str, Any]) -> None:
    """Validate a Validator usage example report JSON for structural integrity.

    Reuses the same contract_version and results validation as the standard
    report validator, with a lighter set of checks.
    """
    if not isinstance(data, dict):
        raise ValidationError("example validator report must be an object")

    required = {"contract_id", "contract_version", "results"}
    missing = missing_fields(data, required)
    if missing:
        raise ValidationError(f"example validator report missing required fields: {', '.join(missing)}")
    for key in ("contract_id", "contract_version"):
        require_string(data, key)

    results = data.get("results")
    if not isinstance(results, list) or not results:
        raise ValidationError("results must be a non-empty array")

    for index, result in enumerate(results):
        if not isinstance(result, dict):
            raise ValidationError(f"results[{index}] must be an object")
        for field in ("artifact_path", "artifact_kind"):
            if field not in result or not isinstance(result[field], str) or not result[field].strip():
                raise ValidationError(f"results[{index}].{field} must be a non-empty string")
        if "overall_verdict" not in result:
            raise ValidationError(f"results[{index}].overall_verdict is required")
        if "findings" not in result:
            raise ValidationError(f"results[{index}].findings is required")
        overall = result.get("overall_verdict")
        if overall not in VALID_OVERALL_VERDICTS:
            raise ValidationError(
                f"results[{index}].overall_verdict must be one of {sorted(VALID_OVERALL_VERDICTS)}, got {overall!r}"
            )
        findings = result.get("findings")
        if not isinstance(findings, list) or not findings:
            raise ValidationError(f"results[{index}].findings must be a non-empty array")
        for fidx, finding in enumerate(findings):
            if not isinstance(finding, dict):
                raise ValidationError(f"results[{index}].findings[{fidx}] must be an object")
            for ff in ("rule_id", "severity", "status", "message"):
                require_string(finding, ff)
            if finding["severity"] not in VALID_FINDING_SEVERITIES:
                raise ValidationError(
                    f"results[{index}].findings[{fidx}].severity must be one of {sorted(VALID_FINDING_SEVERITIES)}, got {finding['severity']!r}"
                )
            if finding["status"] not in VALID_FINDING_STATUSES:
                raise ValidationError(
                    f"results[{index}].findings[{fidx}].status must be one of {sorted(VALID_FINDING_STATUSES)}, got {finding['status']!r}"
                )


def collect_artifacts(project_root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    artifacts: list[tuple[str, pathlib.Path]] = []
    for lane in sorted(VALID_LANES):
        inbox = project_root / "docs" / lane / "inbox"
        epic_dir = project_root / "docs" / lane / "epics"
        outbox = project_root / "docs" / lane / "outbox"
        if inbox.exists():
            artifacts.extend(("ticket", path) for path in sorted(inbox.glob("*.md")))
        if epic_dir.exists():
            artifacts.extend(("epic", path) for path in sorted(epic_dir.glob("*.md")))
        if outbox.exists():
            artifacts.extend(("result", path) for path in sorted(outbox.glob("*.result.json")))
    examples = project_root / "examples"
    if examples.exists():
        artifacts.extend(("queue", path) for path in sorted(examples.glob("**/queue.json")))
    # Also validate Validation Report and Contract JSON files under examples
    if examples.exists():
        artifacts.extend(("report", path) for path in sorted(examples.glob("**/report.json")))
    if examples.exists():
        artifacts.extend(("contract", path) for path in sorted(examples.glob("**/contract.json")))
    # Validate field mapping contract fixtures under examples
    if examples.exists():
        fixture_dir = examples / "field_mapping_contract_fixtures"
        if fixture_dir.exists():
            artifacts.extend(("fixture", path) for path in sorted(fixture_dir.glob("fixture-*.json")))
    # Validate Validator usage example inputs and reports under examples
    if examples.exists():
        for item in sorted(examples.iterdir()):
            if not item.is_dir():
                continue
            # Collect validator-input.json files (example Validator dispatch inputs)
            vi_input = item / "validator-input.json"
            if vi_input.exists():
                artifacts.append(("example-validator-input", vi_input))
            planner_input = item / "planner-validator-input.json"
            if planner_input.exists():
                artifacts.append(("example-validator-input", planner_input))
            # Collect validation-report.json files (example Validator outputs)
            vi_report = item / "validation-report.json"
            if vi_report.exists():
                artifacts.append(("example-validator-report", vi_report))
    return artifacts


def run_validation(project_root: pathlib.Path) -> dict[str, Any]:
    def validate_fixture(path: pathlib.Path) -> None:
        payload = load_json(path)
        validate_field_mapping_fixture(payload)

    def validate_example_validator_input(path: pathlib.Path) -> None:
        payload = load_json(path)
        validate_example_validator_input_func(payload)

    def validate_example_validator_report(path: pathlib.Path) -> None:
        payload = load_json(path)
        validate_example_validator_report_func(payload)

    validators = {
        "ticket": validate_ticket,
        "epic": validate_epic,
        "result": validate_result,
        "queue": validate_queue,
        "report": validate_report,
        "contract": validate_contract,
        "fixture": validate_fixture,
        "example-validator-input": validate_example_validator_input,
        "example-validator-report": validate_example_validator_report,
    }
    checked: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    counts = {kind: 0 for kind in validators}

    for kind, path in collect_artifacts(project_root):
        counts[kind] += 1
        try:
            validators[kind](path)
        except Exception as exc:
            errors.append({"kind": kind, "path": relative(path, project_root), "error": str(exc)})
        else:
            checked.append({"kind": kind, "path": relative(path, project_root)})

    return {
        "status": "ok" if not errors else "failed",
        "project_root": str(project_root),
        "counts": counts,
        "checked": checked,
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Railyard workflow artifacts and example queues.")
    parser.add_argument("--project-root", default=str(ROOT), help="Repository or disposable project root to validate.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = pathlib.Path(args.project_root).resolve()
    payload = run_validation(project_root)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
