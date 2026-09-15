#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

# Ensure scripts directory is importable for direct execution
_scripts_dir = pathlib.Path(__file__).resolve().parent
if str(_scripts_dir.parent) not in sys.path:
    sys.path.insert(0, str(_scripts_dir.parent))

from scripts.governance_read_router import validate_governance_configuration, GovernanceRoutingConfigurationError


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
VALID_VALIDATOR_RISK_LEVELS = {"low", "medium", "high"}
VALIDATOR_REQUIRED_METADATA_FIELDS = {
    "validator_risk_level",
    "validator_contract_source",
    "validator_expected_artifacts",
    "validator_evidence_pack",
    "validator_failure_behavior",
}
FIELD_MAPPING_ROOT_FIELDS = {
    "contract_id", "version", "applies_to", "validation_scope",
    "field_mappings", "derived_field", "source_path",
}
FIELD_MAPPING_OBJECT_FIELDS = {"source_path", "derived_path", "transform"}
FIELD_MAPPING_FIXTURE_FIELDS = {
    "fixture_id", "description", "source_artifact",
    "derived_artifact", "field_mapping_contract", "expected_validation",
}
PRIMITIVE_FIXTURE_REQUIRED_FIELDS = {
    "fixture_id", "primitive_id", "rule_id",
    "registry_section", "expected_verdict", "expected_decisive_findings",
}
PRIMITIVE_FIXTURE_REQUIRED_DIR_FILES = {
    "primitive-fixture.json",
    "validator-input.json",
    "source.json",
    "derived.json",
    "mapping-contract.json",
}
VALID_PRIMITIVE_IDS = {
    "source_availability",
    "field_mapping_required",
    "value_transform_correctness",
    "signed_numeric_preservation",
    "record_identity_preservation",
    "unmapped_field_availability",
}
PRIMITIVE_FIXTURE_ARTIFACT_TYPES = {"source", "derived", "contract"}
VALID_SEMANTIC_CLAIM_TYPES = {
    "coherence",
    "contradiction",
    "completeness",
    "plausibility",
}
VALID_SEMANTIC_EVIDENCE_STATES = {
    "enough_evidence",
    "missing_evidence",
    "conflicting_evidence",
    "unsupported_semantic_claim",
}
SEMANTIC_FIXTURE_REQUIRED_FIELDS = {
    "contract_id",
    "version",
    "applies_to",
    "description",
    "claims",
}
SEMANTIC_CLAIM_REQUIRED_FIELDS = {
    "claim_id",
    "claim_type",
    "description",
    "primary_artifact",
    "assertion",
    "expected_evidence",
    "evidence_state",
    "expected_verdict",
    "expected_status",
}
SEMANTIC_SCOPE_FIELD_BY_CLAIM_TYPE = {
    "coherence": "coherence_scope",
    "contradiction": "contradiction_domain",
    "completeness": "completeness_scope",
    "plausibility": "plausibility_rules",
}
# Frozen Knowledge Contract fixture fields (v0.8.0)
KNOWLEDGE_ENTRY_COMMON_FIELDS = {
    "entry_id", "entry_type", "level", "visibility", "title", "description",
    "version", "valid_from", "immutable", "provenance", "evidence", "relationships",
}
KNOWLEDGE_ENTRY_OPTIONAL_FIELDS = {"evidence_notes"}
KNOWLEDGE_CONSTRAINT_EXTRA_FIELDS = {"constraint_kind", "scope"}
KNOWLEDGE_PROVENANCE_REQUIRED_FIELDS = {
    "origin_epic", "source_tickets", "governing_contract",
}
KNOWLEDGE_PROVENANCE_OPTIONAL_FIELDS = {"additional_sources"}
ARTIFACT_REF_REQUIRED_FIELDS = {"artifact_id", "artifact_kind"}
ARTIFACT_REF_OPTIONAL_FIELDS = {"artifact_version", "locator", "digest"}
VALID_KNOWLEDGE_LEVELS = {"domain", "capability", "feature", "behavior"}
VALID_KNOWLEDGE_ENTRY_TYPES = {"technical_fact", "constraint"}
VALID_KNOWLEDGE_CONSTRAINT_KINDS = {"invariant", "guard", "rule"}
VALID_KNOWLEDGE_VISIBILITIES = {"public", "project", "restricted"}
VALID_KNOWLEDGE_RELATIONSHIPS = {
    "part_of", "depends_on", "constrained_by", "implemented_by",
    "verified_by", "introduced_by",
}
KNOWLEDGE_RELATIONSHIP_FIELDS = {
    "target_kind", "relationship", "scope_note", "target_entry_id", "target_artifact",
}
VALID_KNOWLEDGE_EVENT_TYPES = {
    "knowledge.confidence_changed", "knowledge.review_required",
    "knowledge.superseded", "knowledge.archived", "knowledge.invalidated",
}
VALID_KNOWLEDGE_ACTOR_ROLES = {
    "knowledge_curator", "architect", "validator", "planner", "human",
}
VALID_KNOWLEDGE_ACTIONS = {
    "none", "review", "supersede", "archive", "restore", "human_decision",
}
KNOWLEDGE_EVENT_REQUIRED_FIELDS = {
    "event_id", "event_type", "schema_version", "occurred_at", "event_order",
    "actor_role", "trigger_artifact", "affected_entry_ids", "prior_state",
    "next_state", "reason", "propagation_chain", "recommended_action",
}
KNOWLEDGE_EVENT_CAUSATION_FIELDS = {"causation_id", "causation_chain"}
KNOWLEDGE_STATE_REQUIRED_FIELDS = {
    "confidence", "review_required", "superseded", "archived", "invalidated",
}
KNOWLEDGE_STATE_OPTIONAL_FIELDS = {"superseded_by_entry_id"}
VALID_KNOWLEDGE_CONFIDENCE = {"high", "medium", "low"}
LEGACY_KNOWLEDGE_ENTRY_FIELDS = {
    "confidence", "functionality_relationships", "supersedes", "superseded_by", "valid_until",
}
VALID_KNOWLEDGE_FIXTURE_OUTCOMES = {"pass", "fail"}
# Level rank for hierarchy checks
_KNOWLEDGE_LEVEL_RANK = {"domain": 1, "capability": 2, "feature": 3, "behavior": 4}
PUBLIC_TEXT_ROOT_FILES = {"README.md", "README.zh-CN.md", "CHANGELOG.md", "SKILL.md"}
LOCALIZED_PUBLIC_TEXT_FILES = {"README.zh-CN.md"}
PUBLIC_TEXT_FORBIDDEN_SUBSTRINGS = {
    ".workbuddy",
    "validator_examples",
    "Railyard-Control",
    "WorkBuddy",
    "ARES",
}
PUBLIC_TEXT_EXCLUDED_PATHS = {"references/roadmap.md"}
PUBLIC_TEXT_EXCLUDED_PREFIXES = {"examples/validator_examples/"}
LOCAL_PATH_PATTERN = re.compile(r"\b[A-Za-z]:[\\/]")

TICKET_REQUIRED_FIELDS = {
    "ticket_id",
    "epic_id",
    "task_mode",
    "task_type",
    "priority",
}
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
    has_validator_required = "validator_required" in frontmatter
    has_validator_gate_reason = "validator_gate_reason" in frontmatter
    if not has_validator_required and not has_validator_gate_reason:
        return
    gate_missing = missing_fields(frontmatter, {"validator_required", "validator_gate_reason"})
    if gate_missing:
        raise ValidationError(
            "partial Validator gate metadata missing required fields: " + ", ".join(gate_missing)
        )
    validator_required = frontmatter["validator_required"].lower()
    if validator_required not in {"true", "false"}:
        raise ValidationError("validator_required must be true or false")
    if validator_required == "true":
        gate_missing = missing_fields(frontmatter, VALIDATOR_REQUIRED_METADATA_FIELDS)
        if gate_missing:
            raise ValidationError(
                "validator_required=true missing gate metadata: " + ", ".join(gate_missing)
            )
        risk_level = frontmatter["validator_risk_level"].lower()
        if risk_level not in VALID_VALIDATOR_RISK_LEVELS:
            raise ValidationError(
                f"invalid validator_risk_level {risk_level!r}; expected one of {sorted(VALID_VALIDATOR_RISK_LEVELS)}"
            )


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
        has_review_context = (
            has_inconclusive
            or has_blocked
            or any(f.get("status") == "fail" for f in findings)
            or bool(result.get("missing_evidence"))
            or bool(str(result.get("notes", "")).strip())
        )
        if not has_review_context:
            raise ValidationError(
                f"results[{index}].overall_verdict=human_review_required requires findings, missing_evidence, or notes explaining the manual review need"
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


def validate_knowledge_fixture(data: dict[str, Any], fixture_path: pathlib.Path) -> None:
    """Validate a self-contained fixture against the frozen Knowledge Contract."""
    if not isinstance(data, dict):
        raise ValidationError("knowledge fixture must be an object")
    for field in ("fixture_id", "description"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValidationError(f"{field} must be a non-empty string")
    expected_outcome = data.get("expected_outcome")
    if not isinstance(expected_outcome, str) or expected_outcome not in VALID_KNOWLEDGE_FIXTURE_OUTCOMES:
        raise ValidationError(
            f"expected_outcome must be one of {sorted(VALID_KNOWLEDGE_FIXTURE_OUTCOMES)}, got {expected_outcome!r}"
        )
    expected_rule = data.get("expected_failure_rule")
    if expected_outcome == "fail" and (not isinstance(expected_rule, str) or not expected_rule.strip()):
        raise ValidationError("expected_outcome=fail requires a non-empty expected_failure_rule")
    if expected_outcome == "pass" and "expected_failure_rule" in data:
        raise ValidationError("expected_outcome=pass forbids expected_failure_rule")

    entry_list = data.get("entries")
    if not isinstance(entry_list, list) or not entry_list:
        raise ValidationError("entries must be a non-empty array")
    events = data.get("lifecycle_events")
    if not isinstance(events, list):
        raise ValidationError("lifecycle_events must be an array")
    inventory = data.get("artifact_inventory")
    if not isinstance(inventory, list) or not inventory:
        raise ValidationError("artifact_inventory must be a non-empty array")

    observed_rules: list[str] = []
    artifact_identities: set[tuple[str, str, str | None]] = set()
    for index, artifact in enumerate(inventory):
        observed_rules.extend(_check_artifact_ref(artifact, f"artifact_inventory[{index}]"))
        identity = _artifact_identity(artifact)
        if identity is not None:
            if identity in artifact_identities:
                observed_rules.append("duplicate-artifact-identity")
            artifact_identities.add(identity)

    entry_ids = [
        entry.get("entry_id") for entry in entry_list
        if isinstance(entry, dict) and isinstance(entry.get("entry_id"), str) and entry["entry_id"].strip()
    ]
    corpus_entry_ids = set(entry_ids)
    if len(entry_ids) != len(corpus_entry_ids):
        observed_rules.append("duplicate-entry-identity")

    for idx, entry in enumerate(entry_list):
        observed_rules.extend(
            _check_knowledge_entry(entry, idx, corpus_entry_ids, inventory)
        )
    observed_rules.extend(_check_cross_entry_semantics(entry_list))
    observed_rules.extend(
        _check_knowledge_lifecycle(events, corpus_entry_ids, inventory)
    )

    seen: set[str] = set()
    unique_rules = [rule for rule in observed_rules if not (rule in seen or seen.add(rule))]
    if expected_outcome == "pass":
        if unique_rules:
            raise ValidationError(
                f"expected_outcome=pass but observed rules: {', '.join(unique_rules)}"
            )
    elif unique_rules != [expected_rule]:
        raise ValidationError(
            f"expected_failure_rule={expected_rule!r}; observed rules: "
            f"{', '.join(unique_rules) if unique_rules else '(none)'}"
        )


def _artifact_identity(value: Any) -> tuple[str, str, str | None] | None:
    if not isinstance(value, dict):
        return None
    artifact_id = value.get("artifact_id")
    artifact_kind = value.get("artifact_kind")
    version = value.get("artifact_version")
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        return None
    if not isinstance(artifact_kind, str) or not artifact_kind.strip():
        return None
    if version is not None and (not isinstance(version, str) or not version.strip()):
        return None
    return artifact_kind, artifact_id, version


def _check_artifact_ref(value: Any, path: str) -> list[str]:
    del path
    if not isinstance(value, dict):
        return ["artifact-ref-shape"]
    allowed = ARTIFACT_REF_REQUIRED_FIELDS | ARTIFACT_REF_OPTIONAL_FIELDS
    if set(value) - allowed or missing_fields(value, ARTIFACT_REF_REQUIRED_FIELDS):
        return ["artifact-ref-shape"]
    for field in allowed & set(value):
        if not isinstance(value[field], str) or not value[field].strip():
            return ["artifact-ref-shape"]
    locator = value.get("locator")
    if isinstance(locator, str) and (
        LOCAL_PATH_PATTERN.search(locator) or locator.startswith(("/", "\\"))
    ):
        return ["artifact-ref-portability"]
    digest = value.get("digest")
    if isinstance(digest, str) and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*:[A-Fa-f0-9]+", digest):
        return ["artifact-ref-digest"]
    return []


def _check_resolved_artifact_ref(
    value: Any,
    path: str,
    artifact_inventory: list[Any],
) -> list[str]:
    rules = _check_artifact_ref(value, path)
    identity = _artifact_identity(value)
    if not rules and identity is not None:
        kind, artifact_id, version = identity
        candidates = [
            candidate for candidate in artifact_inventory
            if (candidate_identity := _artifact_identity(candidate)) is not None
            and candidate_identity[0] == kind and candidate_identity[1] == artifact_id
            and (version is None or candidate_identity[2] == version)
        ]
        if len(candidates) != 1:
            rules.append("artifact-ref-resolution")
        else:
            for supplement in ("locator", "digest"):
                if supplement in value and supplement in candidates[0] and value[supplement] != candidates[0][supplement]:
                    rules.append("artifact-ref-supplement-mismatch")
                    break
    return rules


def _check_knowledge_entry(
    entry: dict[str, Any],
    index: int,
    corpus_entry_ids: set[str],
    artifact_inventory: list[Any],
) -> list[str]:
    """Return contract rule IDs violated by one accepted entry."""
    prefix = f"entries[{index}]"
    rules: list[str] = []
    if not isinstance(entry, dict):
        return ["entry-shape"]
    legacy = set(entry) & LEGACY_KNOWLEDGE_ENTRY_FIELDS
    if legacy:
        rules.append("legacy-entry-field")
    entry_type = entry.get("entry_type")
    allowed = KNOWLEDGE_ENTRY_COMMON_FIELDS | KNOWLEDGE_ENTRY_OPTIONAL_FIELDS
    if entry_type == "constraint":
        allowed |= KNOWLEDGE_CONSTRAINT_EXTRA_FIELDS
    if set(entry) - allowed - LEGACY_KNOWLEDGE_ENTRY_FIELDS:
        rules.append("entry-noncanonical-field")
    missing = missing_fields(entry, KNOWLEDGE_ENTRY_COMMON_FIELDS)
    if missing:
        for field in missing:
            rules.append("visibility-required" if field == "visibility" else f"required-field-{field}")
    if not isinstance(entry_type, str) or entry_type not in VALID_KNOWLEDGE_ENTRY_TYPES:
        rules.append("entry-type-enum")
    level = entry.get("level")
    if not isinstance(level, str) or level not in VALID_KNOWLEDGE_LEVELS:
        rules.append("level-enum")
    visibility = entry.get("visibility")
    if not isinstance(visibility, str) or visibility not in VALID_KNOWLEDGE_VISIBILITIES:
        rules.append("visibility-enum")
    for field in ("entry_id", "title", "description", "version", "valid_from"):
        if field in entry and (not isinstance(entry[field], str) or not entry[field].strip()):
            rules.append(f"required-field-{field}")
    if isinstance(entry.get("valid_from"), str) and entry["valid_from"].strip() and not _is_valid_from(entry["valid_from"]):
        rules.append("valid-from-format")
    if not isinstance(entry.get("immutable"), bool):
        rules.append("immutable-type")
    notes = entry.get("evidence_notes")
    if notes is not None and (
        not isinstance(notes, list)
        or not all(isinstance(note, str) and note.strip() for note in notes)
    ):
        rules.append("evidence-notes-shape")

    evidence = entry.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        rules.append("evidence-required")
    else:
        evidence_ids: list[tuple[str, str, str | None]] = []
        for evidx, ref in enumerate(evidence):
            rules.extend(_check_resolved_artifact_ref(ref, f"{prefix}.evidence[{evidx}]", artifact_inventory))
            identity = _artifact_identity(ref)
            if identity is not None:
                evidence_ids.append(identity)
        if len(evidence_ids) != len(set(evidence_ids)):
            rules.append("duplicate-artifact-reference")

    if entry_type == "constraint":
        if missing_fields(entry, KNOWLEDGE_CONSTRAINT_EXTRA_FIELDS):
            rules.append("constraint-required-fields")
        constraint_kind = entry.get("constraint_kind")
        if not isinstance(constraint_kind, str) or constraint_kind not in VALID_KNOWLEDGE_CONSTRAINT_KINDS:
            rules.append("constraint-kind-enum")
        if "scope" in entry and (not isinstance(entry["scope"], str) or not entry["scope"].strip()):
            rules.append("constraint-required-fields")

    provenance = entry.get("provenance")
    if not isinstance(provenance, dict):
        rules.append("provenance-shape")
    else:
        allowed_prov = KNOWLEDGE_PROVENANCE_REQUIRED_FIELDS | KNOWLEDGE_PROVENANCE_OPTIONAL_FIELDS
        if set(provenance) - allowed_prov:
            rules.append("provenance-noncanonical-field")
        if missing_fields(provenance, KNOWLEDGE_PROVENANCE_REQUIRED_FIELDS):
            rules.append("provenance-required-fields")
        origin = provenance.get("origin_epic")
        rules.extend(_check_resolved_artifact_ref(origin, f"{prefix}.provenance.origin_epic", artifact_inventory))
        if isinstance(origin, dict) and origin.get("artifact_kind") != "epic":
            rules.append("provenance-artifact-kind")
        governing = provenance.get("governing_contract")
        rules.extend(_check_resolved_artifact_ref(governing, f"{prefix}.provenance.governing_contract", artifact_inventory))
        if isinstance(governing, dict) and (
            not isinstance(governing.get("artifact_kind"), str)
            or governing.get("artifact_kind") not in {"contract", "reference"}
        ):
            rules.append("provenance-artifact-kind")
        for field, required, kinds in (
            ("source_tickets", True, {"ticket"}),
            ("additional_sources", False, None),
        ):
            refs = provenance.get(field)
            if refs is None and not required:
                continue
            if not isinstance(refs, list) or (required and not refs):
                rules.append("provenance-source-array")
                continue
            identities: list[tuple[str, str, str | None]] = []
            for refidx, ref in enumerate(refs):
                rules.extend(_check_resolved_artifact_ref(ref, f"{prefix}.provenance.{field}[{refidx}]", artifact_inventory))
                if kinds and isinstance(ref, dict) and (
                    not isinstance(ref.get("artifact_kind"), str) or ref.get("artifact_kind") not in kinds
                ):
                    rules.append("provenance-artifact-kind")
                identity = _artifact_identity(ref)
                if identity is not None:
                    identities.append(identity)
            if len(identities) != len(set(identities)):
                rules.append("duplicate-artifact-reference")

    relationships = entry.get("relationships")
    if not isinstance(relationships, list):
        rules.append("relationships-shape")
    else:
        for ridx, rel in enumerate(relationships):
            if not isinstance(rel, dict):
                rules.append("relationship-shape")
                continue
            if set(rel) - KNOWLEDGE_RELATIONSHIP_FIELDS:
                rules.append("relationship-shape")
            if "scope_note" in rel and (
                not isinstance(rel["scope_note"], str) or not rel["scope_note"].strip()
            ):
                rules.append("relationship-shape")
            target_kind = rel.get("target_kind")
            relationship = rel.get("relationship")
            if target_kind not in ("knowledge_entry", "runtime_artifact"):
                rules.append("relationship-target-kind")
                continue
            if not isinstance(relationship, str) or relationship not in VALID_KNOWLEDGE_RELATIONSHIPS:
                rules.append("relationship-type-enum")
                continue
            if target_kind == "knowledge_entry":
                target_id = rel.get("target_entry_id")
                if "target_artifact" in rel or not isinstance(target_id, str) or not target_id.strip():
                    rules.append("relationship-branch-collision")
                elif target_id not in corpus_entry_ids:
                    rules.append("relationship-target-resolution")
                elif target_id == entry.get("entry_id"):
                    rules.append("relationship-self-target")
            else:
                target_artifact = rel.get("target_artifact")
                if "target_entry_id" in rel or target_artifact is None:
                    rules.append("relationship-branch-collision")
                else:
                    rules.extend(_check_resolved_artifact_ref(target_artifact, f"{prefix}.relationships[{ridx}].target_artifact", artifact_inventory))
            if relationship in {"part_of", "depends_on", "constrained_by"} and target_kind != "knowledge_entry":
                rules.append("relationship-target-constraint")
            if relationship in {"implemented_by", "verified_by", "introduced_by"} and target_kind != "runtime_artifact":
                rules.append("relationship-target-constraint")
            if target_kind == "runtime_artifact" and isinstance(rel.get("target_artifact"), dict):
                kind = rel["target_artifact"].get("artifact_kind")
                allowed_kinds = {
                    "implemented_by": {"ticket", "script", "reference"},
                    "verified_by": {"validation_report"},
                    "introduced_by": {"epic", "ticket"},
                }.get(relationship)
                if allowed_kinds is not None and (not isinstance(kind, str) or kind not in allowed_kinds):
                    rules.append("relationship-artifact-kind")
    return rules


def _check_cross_entry_semantics(
    entry_list: list[dict[str, Any]],
) -> list[str]:
    """Check relationship target typing, hierarchy cardinality, roots, and cycles."""
    rules: list[str] = []
    entries_by_id = {
        entry["entry_id"]: entry for entry in entry_list
        if isinstance(entry, dict) and isinstance(entry.get("entry_id"), str)
    }
    part_graph: dict[str, list[str]] = {}
    depends_graph: dict[str, list[str]] = {}
    for entry in entry_list:
        if not isinstance(entry, dict):
            continue
        source_id = entry.get("entry_id", "")
        source_level = entry.get("level")
        rels = entry.get("relationships", [])
        if not isinstance(rels, list):
            continue
        parents: list[str] = []
        for rel in rels:
            if isinstance(rel, dict) and rel.get("target_kind") == "knowledge_entry":
                target_id = rel.get("target_entry_id")
                target = entries_by_id.get(target_id) if isinstance(target_id, str) else None
                if rel.get("relationship") == "part_of" and isinstance(target_id, str):
                    parents.append(target_id)
                    part_graph.setdefault(source_id, []).append(target_id)
                    target_level = target.get("level") if target else None
                    if isinstance(source_level, str) and isinstance(target_level, str) and source_level in _KNOWLEDGE_LEVEL_RANK and target_level in _KNOWLEDGE_LEVEL_RANK:
                        if _KNOWLEDGE_LEVEL_RANK[target_level] != _KNOWLEDGE_LEVEL_RANK[source_level] - 1:
                            rules.append("hierarchy-adjacency")
                if rel.get("relationship") == "depends_on" and isinstance(target_id, str):
                    depends_graph.setdefault(source_id, []).append(target_id)
                if rel.get("relationship") == "constrained_by" and target is not None:
                    if target.get("entry_type") != "constraint":
                        rules.append("constrained-by-target-type")
        if source_level == "domain" and parents:
            rules.append("hierarchy-root-parent")
        elif isinstance(source_level, str) and source_level in {"capability", "feature", "behavior"} and len(parents) != 1:
            rules.append("hierarchy-parent-cardinality")
    if _has_cycle(part_graph):
        rules.append("part-of-cycle")
    if _has_cycle(depends_graph):
        rules.append("depends-on-cycle")
    for source_id in entries_by_id:
        if not _part_of_terminates_at_domain(source_id, part_graph, entries_by_id):
            rules.append("hierarchy-root-resolution")
    return rules


def _part_of_terminates_at_domain(
    source_id: str,
    graph: dict[str, list[str]],
    entries_by_id: dict[str, dict[str, Any]],
) -> bool:
    seen: set[str] = set()
    current = source_id
    while current not in seen:
        seen.add(current)
        entry = entries_by_id.get(current)
        if entry is None:
            return False
        parents = graph.get(current, [])
        if entry.get("level") == "domain":
            return not parents
        if len(parents) != 1:
            return False
        current = parents[0]
    return False


def _check_knowledge_state(state: Any, corpus_entry_ids: set[str]) -> list[str]:
    if not isinstance(state, dict):
        return ["lifecycle-state-shape"]
    allowed = KNOWLEDGE_STATE_REQUIRED_FIELDS | KNOWLEDGE_STATE_OPTIONAL_FIELDS
    if set(state) - allowed or missing_fields(state, KNOWLEDGE_STATE_REQUIRED_FIELDS):
        return ["lifecycle-state-shape"]
    rules: list[str] = []
    confidence = state.get("confidence")
    if not isinstance(confidence, str) or confidence not in VALID_KNOWLEDGE_CONFIDENCE:
        rules.append("lifecycle-state-shape")
    for field in ("review_required", "superseded", "archived", "invalidated"):
        if not isinstance(state.get(field), bool):
            rules.append("lifecycle-state-shape")
    replacement = state.get("superseded_by_entry_id")
    if state.get("superseded") is True:
        if not isinstance(replacement, str) or replacement not in corpus_entry_ids:
            rules.append("lifecycle-supersession-target")
    elif "superseded_by_entry_id" in state:
        rules.append("lifecycle-supersession-target")
    return rules


def _is_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        from datetime import datetime
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return "T" in value
    except ValueError:
        return False


def _is_valid_from(value: str) -> bool:
    if re.fullmatch(r"v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?", value):
        return True
    try:
        from datetime import date, datetime
        if "T" in value:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _check_knowledge_lifecycle(
    events: list[Any],
    corpus_entry_ids: set[str],
    artifact_inventory: list[Any],
) -> list[str]:
    rules: list[str] = []
    event_ids: list[str] = []
    orders: list[int] = []
    parsed_events: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            rules.append("lifecycle-event-shape")
            continue
        parsed_events.append(event)
        allowed = KNOWLEDGE_EVENT_REQUIRED_FIELDS | KNOWLEDGE_EVENT_CAUSATION_FIELDS
        if set(event) - allowed or missing_fields(event, KNOWLEDGE_EVENT_REQUIRED_FIELDS):
            rules.append("lifecycle-event-shape")
        for field in ("event_id", "schema_version", "reason"):
            if not isinstance(event.get(field), str) or not event[field].strip():
                rules.append("lifecycle-event-shape")
        event_type = event.get("event_type")
        if not isinstance(event_type, str) or event_type not in VALID_KNOWLEDGE_EVENT_TYPES:
            rules.append("lifecycle-event-type")
        actor_role = event.get("actor_role")
        if not isinstance(actor_role, str) or actor_role not in VALID_KNOWLEDGE_ACTOR_ROLES:
            rules.append("lifecycle-actor-role")
        recommended_action = event.get("recommended_action")
        if not isinstance(recommended_action, str) or recommended_action not in VALID_KNOWLEDGE_ACTIONS:
            rules.append("lifecycle-recommended-action")
        if not _is_iso_timestamp(event.get("occurred_at")):
            rules.append("lifecycle-occurred-at")
        order = event.get("event_order")
        if not isinstance(order, int) or isinstance(order, bool) or order <= 0:
            rules.append("lifecycle-event-order")
        else:
            orders.append(order)
        event_id = event.get("event_id")
        if isinstance(event_id, str) and event_id.strip():
            event_ids.append(event_id)
        has_id = "causation_id" in event
        has_chain = "causation_chain" in event
        if has_id == has_chain:
            rules.append("lifecycle-causation-xor")
        affected = event.get("affected_entry_ids")
        propagation = event.get("propagation_chain")
        if not isinstance(affected, list) or not affected or not all(
            isinstance(value, str) and value in corpus_entry_ids for value in affected
        ) or len(affected) != len(set(affected)):
            rules.append("lifecycle-affected-entry-resolution")
        if not isinstance(propagation, list) or not all(
            isinstance(value, str) and value in corpus_entry_ids for value in propagation
        ) or len(propagation) != len(set(propagation)):
            rules.append("lifecycle-propagation-resolution")
        elif isinstance(affected, list) and set(propagation) & set(affected):
            rules.append("lifecycle-propagation-resolution")
        rules.extend(_check_resolved_artifact_ref(event.get("trigger_artifact"), f"lifecycle_events[{index}].trigger_artifact", artifact_inventory))
        rules.extend(_check_knowledge_state(event.get("prior_state"), corpus_entry_ids))
        rules.extend(_check_knowledge_state(event.get("next_state"), corpus_entry_ids))
        if event.get("prior_state") == event.get("next_state"):
            rules.append("lifecycle-no-state-change")
        if isinstance(event.get("prior_state"), dict) and isinstance(event.get("next_state"), dict):
            transition_field = {
                "knowledge.confidence_changed": "confidence",
                "knowledge.review_required": "review_required",
                "knowledge.superseded": "superseded",
                "knowledge.archived": "archived",
                "knowledge.invalidated": "invalidated",
            }.get(event_type) if isinstance(event_type, str) else None
            if transition_field and event["prior_state"].get(transition_field) == event["next_state"].get(transition_field):
                rules.append("lifecycle-event-transition")

    if len(event_ids) != len(set(event_ids)):
        rules.append("duplicate-event-identity")
    if len(orders) != len(set(orders)) or orders != sorted(orders):
        rules.append("lifecycle-event-order")

    seen_ids: list[str] = []
    for index, event in enumerate(parsed_events):
        if "causation_chain" in event:
            chain = event.get("causation_chain")
            if not isinstance(chain, list) or not all(isinstance(value, str) for value in chain):
                rules.append("lifecycle-causation")
            elif index == 0 and chain:
                rules.append("lifecycle-causation")
            elif index > 0 and (
                not chain
                or len(chain) != len(set(chain))
                or any(value not in seen_ids for value in chain)
                or [seen_ids.index(value) for value in chain] != sorted(seen_ids.index(value) for value in chain)
            ):
                rules.append("lifecycle-causation")
        elif "causation_id" in event:
            causation_id = event.get("causation_id")
            if index == 0 or not isinstance(causation_id, str) or not seen_ids or causation_id != seen_ids[-1]:
                rules.append("lifecycle-causation")
        event_id = event.get("event_id")
        if isinstance(event_id, str):
            seen_ids.append(event_id)

    def replay() -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], bool]:
        projection: dict[str, dict[str, Any]] = {}
        supersession: dict[str, list[str]] = {}
        consistent = True
        for event in sorted(
            parsed_events,
            key=lambda item: item.get("event_order")
            if isinstance(item.get("event_order"), int) and not isinstance(item.get("event_order"), bool)
            else 0,
        ):
            affected = event.get("affected_entry_ids")
            prior = event.get("prior_state")
            next_state = event.get("next_state")
            if not isinstance(affected, list) or not isinstance(prior, dict) or not isinstance(next_state, dict):
                continue
            for entry_id in affected:
                if entry_id in projection and projection[entry_id] != prior:
                    consistent = False
                projection[entry_id] = dict(next_state)
                replacement = next_state.get("superseded_by_entry_id")
                if next_state.get("superseded") is True and isinstance(replacement, str):
                    supersession.setdefault(entry_id, []).append(replacement)
        return projection, supersession, consistent

    projection_one, supersession_graph, consistent = replay()
    projection_two, _, _ = replay()
    if not consistent or projection_one != projection_two:
        rules.append("lifecycle-replay-transition")
    if _has_cycle(supersession_graph):
        rules.append("lifecycle-supersession-cycle")
    return rules


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    """Detect if a directed graph has a cycle using DFS."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for neighbor in graph.get(node, []):
            if color.get(neighbor, WHITE) == GRAY:
                return True
            if color.get(neighbor, WHITE) == WHITE:
                if dfs(neighbor):
                    return True
        color[node] = BLACK
        return False

    for node in graph:
        if color.get(node, WHITE) == WHITE:
            if dfs(node):
                return True
    return False


def validate_primitive_fixture(
    data: dict[str, Any],
    fixture_dir: pathlib.Path,
    project_root: pathlib.Path,
) -> None:
    """Validate a primitive fixture metadata JSON against the v0.7.2 shape.

    Validates:
      - Required JSON fields are present
      - primitive_id == rule_id
      - expected_decisive_findings[*].rule_id == primitive_id
      - expected_verdict is a valid verdict
      - Directory contains all required files (or documented absence)
      - validator-input.json artifact paths resolve relative to project_root
    """
    if not isinstance(data, dict):
        raise ValidationError("primitive fixture must be an object")

    missing = missing_fields(data, PRIMITIVE_FIXTURE_REQUIRED_FIELDS)
    if missing:
        raise ValidationError(f"primitive fixture missing required fields: {', '.join(missing)}")

    require_string(data, "fixture_id")
    require_string(data, "primitive_id")
    require_string(data, "rule_id")
    require_string(data, "registry_section")

    primitive_id = data["primitive_id"]
    if primitive_id not in VALID_PRIMITIVE_IDS:
        raise ValidationError(
            f"primitive_id must be one of {sorted(VALID_PRIMITIVE_IDS)}, got {primitive_id!r}"
        )

    rule_id = data["rule_id"]
    if rule_id != primitive_id:
        raise ValidationError(
            f"rule_id {rule_id!r} must equal primitive_id {primitive_id!r}"
        )

    verdict = data["expected_verdict"]
    if verdict not in VALID_OVERALL_VERDICTS:
        raise ValidationError(
            f"expected_verdict must be one of {sorted(VALID_OVERALL_VERDICTS)}, got {verdict!r}"
        )

    findings = data["expected_decisive_findings"]
    if not isinstance(findings, list) or not findings:
        raise ValidationError("expected_decisive_findings must be a non-empty array")
    for fidx, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValidationError(f"expected_decisive_findings[{fidx}] must be an object")
        require_string(finding, "rule_id")
        require_string(finding, "status")
        finding_rule_id = finding["rule_id"]
        if finding_rule_id != primitive_id:
            raise ValidationError(
                f"expected_decisive_findings[{fidx}].rule_id {finding_rule_id!r} "
                f"must equal primitive_id {primitive_id!r}"
            )
        if finding["status"] not in VALID_FINDING_STATUSES and finding["status"] != "absent":
            raise ValidationError(
                f"expected_decisive_findings[{fidx}].status must be one of "
                f"{sorted(VALID_FINDING_STATUSES)} or 'absent', got {finding['status']!r}"
            )
        if "severity" in finding:
            if finding["severity"] not in VALID_FINDING_SEVERITIES:
                raise ValidationError(
                    f"expected_decisive_findings[{fidx}].severity must be one of "
                    f"{sorted(VALID_FINDING_SEVERITIES)}, got {finding['severity']!r}"
                )

    # -- Directory completeness --
    intentionally_missing = set(data.get("intentionally_missing_artifacts", []))
    invalid_types = intentionally_missing - PRIMITIVE_FIXTURE_ARTIFACT_TYPES
    if invalid_types:
        raise ValidationError(
            f"intentionally_missing_artifacts contains invalid types: {sorted(invalid_types)}"
        )

    file_type_map = {
        "source": "source.json",
        "derived": "derived.json",
        "contract": "mapping-contract.json",
    }
    for art_type, filename in file_type_map.items():
        expected_path = fixture_dir / filename
        present = expected_path.exists()
        if art_type in intentionally_missing:
            if present:
                raise ValidationError(
                    f"Artifact {filename} exists but is declared intentionally_missing"
                )
        else:
            if not present:
                raise ValidationError(
                    f"Required artifact {filename} is missing "
                    f"and not listed in intentionally_missing_artifacts"
                )

    # -- validator-input.json must exist at fixture_dir --
    vi_path = fixture_dir / "validator-input.json"
    if not vi_path.exists():
        raise ValidationError(f"validator-input.json not found at {vi_path}")

    # -- validator-input.json artifact paths must resolve --
    try:
        vi_data = json.loads(vi_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"validator-input.json is invalid JSON: {exc}") from exc

    if not isinstance(vi_data, dict):
        raise ValidationError("validator-input.json must be a JSON object")

    artifacts = vi_data.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValidationError("validator-input.json artifacts must be an array")

    for idx, entry in enumerate(artifacts):
        if not isinstance(entry, dict):
            raise ValidationError(f"validator-input.json artifacts[{idx}] must be an object")
        path_val = entry.get("path")
        if not isinstance(path_val, str) or not path_val.strip():
            raise ValidationError(
                f"validator-input.json artifacts[{idx}].path must be a non-empty string"
            )
        resolved = (project_root / path_val).resolve()
        if not resolved.exists():
            raise ValidationError(
                f"validator-input.json artifacts[{idx}].path {path_val!r} "
                f"does not resolve to an existing file (resolved: {resolved})"
            )


def validate_semantic_fixture(data: dict[str, Any]) -> None:
    """Validate a v0.7.4 semantic calibration fixture.

    Semantic calibration fixtures are non-executable reference artifacts. This
    validator checks their declared contract shape, evidence states, and
    expected verdict branches. It does not perform semantic inference.
    """
    if not isinstance(data, dict):
        raise ValidationError("semantic fixture must be an object")

    missing = missing_fields(data, SEMANTIC_FIXTURE_REQUIRED_FIELDS)
    if missing:
        raise ValidationError(f"semantic fixture missing required fields: {', '.join(missing)}")

    for field in ("contract_id", "version", "description"):
        require_string(data, field)

    applies_to = data["applies_to"]
    if not isinstance(applies_to, list) or "semantic_calibration_fixture" not in applies_to:
        raise ValidationError("applies_to must include 'semantic_calibration_fixture'")

    claims = data["claims"]
    if not isinstance(claims, list) or not claims:
        raise ValidationError("claims must be a non-empty array")

    claim_types: set[str] = set()
    evidence_states: set[str] = set()
    enough_verdicts: set[str] = set()
    for cidx, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ValidationError(f"claims[{cidx}] must be an object")
        missing_claim = missing_fields(claim, SEMANTIC_CLAIM_REQUIRED_FIELDS)
        if missing_claim:
            raise ValidationError(
                f"claims[{cidx}] missing required fields: {', '.join(missing_claim)}"
            )
        for field in (
            "claim_id",
            "claim_type",
            "description",
            "primary_artifact",
            "assertion",
            "evidence_state",
            "expected_verdict",
            "expected_status",
        ):
            require_string(claim, field)

        claim_type = claim["claim_type"]
        if claim_type not in VALID_SEMANTIC_CLAIM_TYPES:
            raise ValidationError(
                f"claims[{cidx}].claim_type must be one of "
                f"{sorted(VALID_SEMANTIC_CLAIM_TYPES)}, got {claim_type!r}"
            )
        claim_types.add(claim_type)

        evidence_state = claim["evidence_state"]
        if evidence_state not in VALID_SEMANTIC_EVIDENCE_STATES:
            raise ValidationError(
                f"claims[{cidx}].evidence_state must be one of "
                f"{sorted(VALID_SEMANTIC_EVIDENCE_STATES)}, got {evidence_state!r}"
            )
        evidence_states.add(evidence_state)

        verdict = claim["expected_verdict"]
        if verdict not in VALID_OVERALL_VERDICTS:
            raise ValidationError(
                f"claims[{cidx}].expected_verdict must be one of "
                f"{sorted(VALID_OVERALL_VERDICTS)}, got {verdict!r}"
            )

        status = claim["expected_status"]
        if status not in VALID_FINDING_STATUSES:
            raise ValidationError(
                f"claims[{cidx}].expected_status must be one of "
                f"{sorted(VALID_FINDING_STATUSES)}, got {status!r}"
            )

        expected_evidence = claim["expected_evidence"]
        if not isinstance(expected_evidence, list) or not expected_evidence:
            raise ValidationError(f"claims[{cidx}].expected_evidence must be a non-empty array")
        if not all(isinstance(item, str) and item.strip() for item in expected_evidence):
            raise ValidationError(f"claims[{cidx}].expected_evidence must contain strings")

        if claim_type in {"coherence", "contradiction"}:
            related = claim.get("related_artifacts")
            if not isinstance(related, list) or not related:
                raise ValidationError(
                    f"claims[{cidx}].related_artifacts must be a non-empty array "
                    f"for {claim_type} claims"
                )

        if evidence_state == "enough_evidence":
            if verdict not in {"pass", "fail"}:
                raise ValidationError(
                    f"claims[{cidx}] enough_evidence must expect pass or fail, got {verdict!r}"
                )
            if status != verdict:
                raise ValidationError(
                    f"claims[{cidx}] enough_evidence expected_status must equal "
                    f"expected_verdict"
                )
            enough_verdicts.add(verdict)
        elif evidence_state in {"missing_evidence", "conflicting_evidence"}:
            if verdict != "inconclusive" or status != "inconclusive":
                raise ValidationError(
                    f"claims[{cidx}] {evidence_state} must expect inconclusive verdict/status"
                )
        elif evidence_state == "unsupported_semantic_claim":
            expected_unsupported = (
                "human_review_required"
                if claim_type in {"coherence", "contradiction"}
                else "inconclusive"
            )
            if verdict != expected_unsupported:
                raise ValidationError(
                    f"claims[{cidx}] unsupported {claim_type} claim must expect "
                    f"{expected_unsupported!r}, got {verdict!r}"
                )

    if len(claim_types) != 1:
        raise ValidationError(
            f"semantic fixture must target exactly one claim_type, got {sorted(claim_types)}"
        )

    claim_type = next(iter(claim_types))
    required_scope = SEMANTIC_SCOPE_FIELD_BY_CLAIM_TYPE[claim_type]
    if required_scope not in data:
        raise ValidationError(f"semantic fixture for {claim_type} missing {required_scope}")

    missing_states = VALID_SEMANTIC_EVIDENCE_STATES - evidence_states
    if missing_states:
        raise ValidationError(
            f"semantic fixture missing evidence states: {sorted(missing_states)}"
        )

    if enough_verdicts != {"pass", "fail"}:
        raise ValidationError(
            "semantic fixture must include enough_evidence examples for both pass and fail"
        )


def validate_public_text(path: pathlib.Path, project_root: pathlib.Path) -> None:
    """Validate public text hygiene.

    Public text is ASCII-only by default. Localized documents are an explicit
    exception: they may contain target-language characters, but still must be
    valid UTF-8 without BOM, replacement characters, private-use mojibake, local
    paths, or private project references.
    """
    data = path.read_bytes()
    rel = relative(path, project_root)
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValidationError("public text must be UTF-8 without BOM")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"public text must be valid UTF-8: {exc}") from exc
    if "\ufffd" in text:
        raise ValidationError("public text contains Unicode replacement character")
    if any(0xE000 <= ord(char) <= 0xF8FF for char in text):
        raise ValidationError("public text contains private-use mojibake characters")
    if rel not in LOCALIZED_PUBLIC_TEXT_FILES and any(ord(char) > 127 for char in text):
        raise ValidationError("public text must be ASCII-only unless localized")
    for marker in sorted(PUBLIC_TEXT_FORBIDDEN_SUBSTRINGS):
        if marker in text:
            raise ValidationError(f"public text contains forbidden marker {marker!r}")
    if LOCAL_PATH_PATTERN.search(text):
        raise ValidationError("public text contains a local absolute path")


def include_public_text(path: pathlib.Path, project_root: pathlib.Path) -> bool:
    rel = relative(path, project_root)
    if rel in PUBLIC_TEXT_EXCLUDED_PATHS:
        return False
    return not any(rel.startswith(prefix) for prefix in PUBLIC_TEXT_EXCLUDED_PREFIXES)


def collect_artifacts(project_root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    artifacts: list[tuple[str, pathlib.Path]] = []
    for filename in sorted(PUBLIC_TEXT_ROOT_FILES):
        path = project_root / filename
        if path.exists() and include_public_text(path, project_root):
            artifacts.append(("public-text", path))
    references = project_root / "references"
    if references.exists():
        artifacts.extend(
            ("public-text", path)
            for path in sorted(references.glob("*.md"))
            if include_public_text(path, project_root)
        )
    examples = project_root / "examples"
    if examples.exists():
        artifacts.extend(
            ("public-text", path)
            for path in sorted(examples.glob("**/*.md"))
            if include_public_text(path, project_root)
        )
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
        artifacts.extend(("ticket", path) for path in sorted(examples.glob("**/DOMAIN-*.md")))
        artifacts.extend(("ticket", path) for path in sorted(examples.glob("**/SYSTEM-*.md")))
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
    # Validate primitive fixture metadata under examples
    if examples.exists():
        primitive_fixture_dir = examples / "primitive_fixtures"
        if primitive_fixture_dir.exists():
            artifacts.extend(
                ("primitive-fixture", path)
                for path in sorted(primitive_fixture_dir.glob("**/primitive-fixture.json"))
            )
    # Validate semantic calibration fixtures under examples
    if examples.exists():
        semantic_fixture_dir = examples / "semantic_calibration_fixtures"
        if semantic_fixture_dir.exists():
            artifacts.extend(
                ("semantic-fixture", path)
                for path in sorted(semantic_fixture_dir.glob("fixture-semantic-*.json"))
            )
    # Validate Knowledge Contract calibration fixtures under examples
    if examples.exists():
        knowledge_fixture_dir = examples / "knowledge_contract_fixtures"
        if knowledge_fixture_dir.exists():
            artifacts.extend(
                ("knowledge-fixture", path)
                for path in sorted(knowledge_fixture_dir.glob("fixture-*.json"))
            )
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

    def validate_primitive_fixture_func(path: pathlib.Path) -> None:
        payload = load_json(path)
        fixture_dir = path.parent
        validate_primitive_fixture(payload, fixture_dir, project_root)

    def validate_semantic_fixture_func(path: pathlib.Path) -> None:
        payload = load_json(path)
        validate_semantic_fixture(payload)

    def validate_knowledge_fixture_func(path: pathlib.Path) -> None:
        payload = load_json(path)
        validate_knowledge_fixture(payload, path)

    validators = {
        "public-text": lambda path: validate_public_text(path, project_root),
        "ticket": validate_ticket,
        "epic": validate_epic,
        "result": validate_result,
        "queue": validate_queue,
        "report": validate_report,
        "contract": validate_contract,
        "fixture": validate_fixture,
        "example-validator-input": validate_example_validator_input,
        "example-validator-report": validate_example_validator_report,
        "primitive-fixture": validate_primitive_fixture_func,
        "semantic-fixture": validate_semantic_fixture_func,
        "knowledge-fixture": validate_knowledge_fixture_func,
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

    governance_config_status = "ok"
    governance_config_error = None
    try:
        validate_governance_configuration(project_root)
    except GovernanceRoutingConfigurationError as exc:
        governance_config_status = "failed"
        governance_config_error = str(exc)

    return {
        "status": "ok" if not errors else "failed",
        "validation_kind": "artifact_shape",
        "independent_validator_evidence": False,
        "project_root": str(project_root),
        "counts": counts,
        "checked": checked,
        "errors": errors,
        "governance_config": {
            "status": governance_config_status,
            "error": governance_config_error,
            "independent_validator_evidence": False,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Railyard workflow artifact shapes and example queues.")
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
