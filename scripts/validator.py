#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
from decimal import Decimal, InvalidOperation
from typing import Any


VALID_VERDICTS = {"pass", "fail", "blocked", "inconclusive", "human_review_required"}
VALID_MISSING_MAPPING_POLICIES = {"inconclusive", "fail", "human_review_required"}
SUPPORTED_TRANSFORMS = {
    "identity",
    "multiply_by_2",
    "parse_integer",
    "parse_number_preserve_sign",
}


class ValidatorError(ValueError):
    pass


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValidatorError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidatorError(f"invalid JSON in {path}: {exc}") from exc


def resolve_path(path_value: str, base_dir: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(path_value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def display_path(path: pathlib.Path, base_dir: pathlib.Path) -> str:
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.as_posix()


def make_finding(
    rule_id: str,
    severity: str,
    status: str,
    message: str,
    evidence: str | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "status": status,
        "message": message,
        "evidence": evidence,
    }


def path_tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for part in path.split("."):
        remainder = part
        while "[" in remainder:
            prefix, _, rest = remainder.partition("[")
            if prefix:
                tokens.append(prefix)
            index, sep, tail = rest.partition("]")
            if not sep or not index.isdigit():
                tokens.append(remainder)
                remainder = ""
                break
            tokens.append(int(index))
            remainder = tail
        if remainder:
            tokens.append(remainder)
    return tokens


def get_path(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for token in path_tokens(path):
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return False, None
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return False, None
            current = current[token]
    return True, current


def as_records(artifact: Any) -> list[dict[str, Any]]:
    if isinstance(artifact, dict):
        records = artifact.get("records")
        if isinstance(records, list):
            return [record for record in records if isinstance(record, dict)]
        for key in ("data", "actual_output", "expected_output"):
            value = artifact.get(key)
            if isinstance(value, dict):
                return [value]
    if isinstance(artifact, list):
        return [record for record in artifact if isinstance(record, dict)]
    return []


def record_fields(records: list[dict[str, Any]]) -> list[str]:
    fields: set[str] = set()
    for record in records:
        fields.update(str(key) for key in record)
    return sorted(fields)


def decimal_value(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise InvalidOperation
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if text.endswith("%"):
            text = text[:-1].strip()
        return Decimal(text)
    raise InvalidOperation


def normalize_number(value: Any) -> Any:
    try:
        return decimal_value(value).normalize()
    except (InvalidOperation, ValueError):
        return value


def same_value(left: Any, right: Any) -> bool:
    left_number = normalize_number(left)
    right_number = normalize_number(right)
    if isinstance(left_number, Decimal) and isinstance(right_number, Decimal):
        return left_number == right_number
    return left == right


def same_sign(left: Any, right: Any) -> bool:
    try:
        left_number = decimal_value(left)
        right_number = decimal_value(right)
    except (InvalidOperation, ValueError):
        return True
    if left_number == 0 or right_number == 0:
        return left_number == right_number
    return (left_number > 0) == (right_number > 0)


def apply_transform(name: str, value: Any) -> Any:
    if name == "identity":
        return value
    if name == "multiply_by_2":
        return decimal_value(value) * Decimal(2)
    if name == "parse_integer":
        parsed = decimal_value(value)
        if parsed != parsed.to_integral_value():
            raise InvalidOperation
        return int(parsed)
    if name == "parse_number_preserve_sign":
        return decimal_value(value)
    raise ValidatorError(f"unsupported transform: {name}")


def mapping_source_path(mapping: dict[str, Any]) -> str | None:
    value = mapping.get("source_path", mapping.get("source_location"))
    return value if isinstance(value, str) and value else None


def mapping_derived_path(mapping: dict[str, Any]) -> str | None:
    value = mapping.get("derived_path", mapping.get("derived_field"))
    return value if isinstance(value, str) and value else None


def mapping_transform(mapping: dict[str, Any]) -> str | None:
    value = mapping.get("transform", mapping.get("expected_transform"))
    return value if isinstance(value, str) and value else None


def find_record_key_mapping(mappings: list[dict[str, Any]]) -> dict[str, Any] | None:
    for mapping in mappings:
        derived = mapping_derived_path(mapping) or ""
        if derived in {"record_id", "record_key", "id"}:
            return mapping
    for mapping in mappings:
        if mapping_transform(mapping) == "identity":
            return mapping
    return mappings[0] if mappings else None


def build_record_pairs(
    source_records: list[dict[str, Any]],
    derived_records: list[dict[str, Any]],
    key_mapping: dict[str, Any] | None,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any] | None, str]], list[str]]:
    if key_mapping is None:
        pairs = []
        for index, source_record in enumerate(source_records):
            derived_record = derived_records[index] if index < len(derived_records) else None
            pairs.append((source_record, derived_record, f"index:{index}"))
        orphan_keys = [f"index:{index}" for index in range(len(source_records), len(derived_records))]
        return pairs, orphan_keys

    source_key_path = mapping_source_path(key_mapping)
    derived_key_path = mapping_derived_path(key_mapping)
    transform = mapping_transform(key_mapping) or "identity"
    if source_key_path is None or derived_key_path is None:
        return build_record_pairs(source_records, derived_records, None)

    derived_by_key: dict[str, dict[str, Any]] = {}
    for record in derived_records:
        found, key = get_path(record, derived_key_path)
        if found:
            derived_by_key[str(key)] = record

    pairs = []
    seen_keys: set[str] = set()
    for source_record in source_records:
        found, source_key = get_path(source_record, source_key_path)
        if not found:
            pairs.append((source_record, None, "<missing-source-key>"))
            continue
        try:
            expected_key = apply_transform(transform, source_key)
        except Exception:
            expected_key = source_key
        key_text = str(expected_key)
        seen_keys.add(key_text)
        pairs.append((source_record, derived_by_key.get(key_text), key_text))

    orphan_keys = sorted(key for key in derived_by_key if key not in seen_keys)
    return pairs, orphan_keys


def artifact_summary_entry(kind: str, path: str, payload: Any) -> dict[str, Any]:
    records = as_records(payload)
    entry: dict[str, Any] = {"kind": kind, "status": "read"}
    if records:
        entry["records"] = len(records)
        entry["fields"] = record_fields(records)
    elif isinstance(payload, dict):
        entry["top_level_fields"] = sorted(str(key) for key in payload)
    else:
        entry["json_type"] = type(payload).__name__
    return entry


def compute_overall_verdict(
    findings: list[dict[str, Any]],
    warnings_as_errors: bool,
    human_review_required: bool,
) -> str:
    if any(f["severity"] == "error" and f["status"] == "fail" for f in findings):
        return "fail"
    if warnings_as_errors and any(f["severity"] == "warn" and f["status"] == "fail" for f in findings):
        return "fail"
    if any(f["status"] == "blocked" for f in findings):
        return "blocked"
    if human_review_required:
        return "human_review_required"
    if any(f["status"] == "inconclusive" for f in findings):
        return "inconclusive"
    return "pass"


def select_contract(
    input_contract: Any,
    artifacts: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str, str]:
    contract_artifacts = [
        item["payload"]
        for item in artifacts.values()
        if item["kind"] == "contract" and isinstance(item["payload"], dict)
    ]
    for contract in contract_artifacts:
        if isinstance(contract.get("field_mappings"), list):
            return contract, str(contract.get("contract_id", "field-mapping-contract")), str(contract.get("version", "unknown"))
    if isinstance(input_contract, dict):
        return (
            input_contract,
            str(input_contract.get("contract_id", "validation-contract")),
            str(input_contract.get("version", "unknown")),
        )
    return None, "unsupported-contract", "unknown"


def validate_source_to_derived(
    input_payload: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    contract: dict[str, Any],
    contract_id: str,
    contract_version: str,
    artifact_summary: dict[str, Any],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    missing_evidence: list[dict[str, str]] = []
    validated_artifacts = [path for path, item in artifacts.items() if item["kind"] in {"source", "derived", "contract"}]
    risk_level = input_payload.get("risk_level", "medium")
    warnings_as_errors = bool(contract.get("warnings_as_errors", False))
    missing_mapping_policy = contract.get("missing_mapping_policy", "inconclusive")
    if missing_mapping_policy not in VALID_MISSING_MAPPING_POLICIES:
        missing_mapping_policy = "inconclusive"

    source_items = [item for item in artifacts.values() if item["kind"] == "source"]
    derived_items = [item for item in artifacts.values() if item["kind"] == "derived"]
    if not source_items or not derived_items:
        findings.append(make_finding(
            "source_derived_artifacts_present",
            "error",
            "blocked",
            "Source-to-derived validation requires one source artifact and one derived artifact.",
            "missing source or derived artifact",
        ))
        return make_report(contract_id, contract_version, artifact_summary, findings, missing_evidence, validated_artifacts)

    source_records = as_records(source_items[0]["payload"])
    derived_records = as_records(derived_items[0]["payload"])
    if not source_records or not derived_records:
        findings.append(make_finding(
            "record_materialization",
            "error",
            "blocked",
            "Source and derived artifacts must contain object records for field-mapping validation.",
            "records arrays or single record objects were not found",
        ))
        return make_report(contract_id, contract_version, artifact_summary, findings, missing_evidence, validated_artifacts)

    mappings_raw = contract.get("field_mappings")
    mappings = [mapping for mapping in mappings_raw if isinstance(mapping, dict)] if isinstance(mappings_raw, list) else []
    if not mappings:
        findings.append(make_finding(
            "field_mapping_required",
            "error",
            "fail",
            "No field_mappings array was available in the field mapping contract.",
            "field_mappings missing or empty",
        ))
        missing_evidence.append({
            "expected": "field_mappings in field mapping contract",
            "impact": "Cannot independently map derived fields to source fields.",
        })
        return make_report(contract_id, contract_version, artifact_summary, findings, missing_evidence, validated_artifacts)

    findings.append(make_finding(
        "candidate_output_not_truth_source",
        "error",
        "pass",
        "Expected values were resolved from the field mapping contract and source artifact, not from derived output.",
        "contract field_mappings and source records used as truth inputs",
    ))

    if len(source_records) == len(derived_records):
        findings.append(make_finding(
            "record_identity_preservation",
            "error",
            "pass",
            "Source and derived record counts match.",
            f"source_records={len(source_records)}; derived_records={len(derived_records)}",
        ))
    else:
        findings.append(make_finding(
            "record_identity_preservation",
            "error",
            "fail",
            "Source and derived record counts do not match.",
            f"source_records={len(source_records)}; derived_records={len(derived_records)}",
        ))

    key_mapping = find_record_key_mapping(mappings)
    pairs, orphan_keys = build_record_pairs(source_records, derived_records, key_mapping)
    if orphan_keys:
        findings.append(make_finding(
            "record_identity_preservation",
            "error",
            "fail",
            "Derived artifact contains records that do not map to source record keys.",
            f"orphan_derived_keys={orphan_keys}",
        ))

    source_mapping_failures = 0
    sign_failures = 0
    unsupported_transforms: set[str] = set()
    derived_paths_declared: set[str] = set()
    missing_record_keys_reported: set[str] = set()

    for mapping in mappings:
        source_path = mapping_source_path(mapping)
        derived_path = mapping_derived_path(mapping)
        transform = mapping_transform(mapping)
        required = mapping.get("required", True) is not False
        preserve_sign = bool(mapping.get("preserve_sign", False))
        if not source_path or not derived_path or not transform:
            if required:
                source_mapping_failures += 1
                findings.append(make_finding(
                    "field_mapping_required",
                    "error",
                    "fail",
                    "Required field mapping is missing source, derived, or transform metadata.",
                    json.dumps(mapping, sort_keys=True),
                ))
            continue
        derived_paths_declared.add(derived_path)
        if transform not in SUPPORTED_TRANSFORMS:
            unsupported_transforms.add(transform)
            findings.append(make_finding(
                "declared_transform_only",
                "warn",
                "fail",
                f"Transform '{transform}' is not supported by this reference implementation.",
                f"derived_path={derived_path}; source_path={source_path}",
            ))
            continue

        for source_record, derived_record, record_key in pairs:
            found_source, source_value = get_path(source_record, source_path)
            if not found_source:
                if required:
                    source_mapping_failures += 1
                    missing_evidence.append({
                        "expected": f"source field {source_path}",
                        "impact": f"Cannot verify derived field {derived_path}.",
                    })
                    findings.append(make_finding(
                        "field_mapping_required",
                        "error",
                        "fail",
                        f"Required source field '{source_path}' is missing.",
                        f"record_key={record_key}",
                    ))
                continue
            if derived_record is None:
                if record_key not in missing_record_keys_reported:
                    source_mapping_failures += 1
                    findings.append(make_finding(
                        "record_identity_preservation",
                        "error",
                        "fail",
                        "Source record has no corresponding derived record.",
                        f"record_key={record_key}",
                    ))
                    missing_record_keys_reported.add(record_key)
                continue
            found_derived, derived_value = get_path(derived_record, derived_path)
            if not found_derived:
                if required:
                    source_mapping_failures += 1
                    missing_evidence.append({
                        "expected": f"derived field {derived_path}",
                        "impact": f"Cannot compare mapped source field {source_path}.",
                    })
                    findings.append(make_finding(
                        "field_mapping_required",
                        "error",
                        "fail",
                        f"Required derived field '{derived_path}' is missing.",
                        f"record_key={record_key}",
                    ))
                continue
            try:
                expected_value = apply_transform(transform, source_value)
            except Exception as exc:
                source_mapping_failures += 1
                findings.append(make_finding(
                    "source_value_preservation",
                    "error",
                    "fail",
                    f"Could not apply transform '{transform}' for derived field '{derived_path}'.",
                    f"record_key={record_key}; source_value={source_value!r}; error={exc}",
                ))
                continue
            if not same_value(expected_value, derived_value):
                source_mapping_failures += 1
                findings.append(make_finding(
                    "source_value_preservation",
                    "error",
                    "fail",
                    f"Derived field '{derived_path}' does not match source field '{source_path}' after '{transform}'.",
                    f"record_key={record_key}; expected={expected_value!r}; observed={derived_value!r}",
                ))
            if preserve_sign and not same_sign(source_value, derived_value):
                sign_failures += 1
                findings.append(make_finding(
                    "signed_numeric_preservation",
                    "error",
                    "fail",
                    f"Derived field '{derived_path}' does not preserve the sign of source field '{source_path}'.",
                    f"record_key={record_key}; source={source_value!r}; observed={derived_value!r}",
                ))

    if source_mapping_failures == 0:
        findings.append(make_finding(
            "source_value_preservation",
            "error",
            "pass",
            "All required mapped fields matched their source values after declared transforms.",
            f"mappings_checked={len(mappings)}",
        ))
    if sign_failures == 0:
        findings.append(make_finding(
            "signed_numeric_preservation",
            "error",
            "pass",
            "All mappings that declare preserve_sign kept numeric signs intact.",
            None,
        ))
    if not unsupported_transforms:
        findings.append(make_finding(
            "declared_transform_only",
            "warn",
            "pass",
            "All declared transforms are supported by this reference implementation.",
            f"supported_transforms={sorted(SUPPORTED_TRANSFORMS)}",
        ))

    derived_fields = set(record_fields(derived_records))
    unmapped_fields = sorted(field for field in derived_fields if field not in derived_paths_declared)
    human_review_required = False
    if unmapped_fields:
        if missing_mapping_policy == "human_review_required":
            status = "inconclusive"
            human_review_required = True
        elif missing_mapping_policy == "fail":
            status = "fail"
        else:
            status = "inconclusive"
        severity = "error" if missing_mapping_policy == "fail" else "warn"
        findings.append(make_finding(
            "field_mapping_required",
            severity,
            status,
            "Derived fields exist without explicit independent source mappings.",
            f"unmapped_derived_fields={unmapped_fields}; policy={missing_mapping_policy}; risk_level={risk_level}",
        ))
        findings.append(make_finding(
            "missing_mapping_policy",
            "warn",
            status,
            f"Applied missing_mapping_policy='{missing_mapping_policy}' to unmapped derived fields.",
            f"unmapped_derived_fields={unmapped_fields}",
        ))
        for field in unmapped_fields:
            missing_evidence.append({
                "expected": f"field mapping for derived field {field}",
                "impact": "Cannot verify the derived value against independent source evidence.",
            })
    else:
        findings.append(make_finding(
            "field_mapping_required",
            "error",
            "pass",
            "Every derived field has an explicit field mapping.",
            f"derived_fields={sorted(derived_fields)}",
        ))
        findings.append(make_finding(
            "missing_mapping_policy",
            "warn",
            "not_applicable",
            "No unmapped derived fields were found.",
            f"policy={missing_mapping_policy}",
        ))

    if warnings_as_errors and any(f["severity"] == "warn" and f["status"] == "fail" for f in findings):
        findings.append(make_finding(
            "warning_policy",
            "error",
            "fail",
            "warnings_as_errors=true escalates warn+fail findings to a failing overall verdict.",
            "warnings_as_errors=true",
        ))
    else:
        findings.append(make_finding(
            "warning_policy",
            "info",
            "pass",
            "Warning-level failures do not force overall failure unless warnings_as_errors=true.",
            f"warnings_as_errors={warnings_as_errors}",
        ))

    overall_verdict = compute_overall_verdict(findings, warnings_as_errors, human_review_required)
    return make_report(
        contract_id,
        contract_version,
        artifact_summary,
        findings,
        missing_evidence,
        validated_artifacts,
        overall_verdict=overall_verdict,
        confidence="high" if overall_verdict in {"pass", "fail"} else "medium",
        notes="Source-to-derived field mapping validation completed using only the input JSON and referenced artifacts.",
    )


def make_report(
    contract_id: str,
    contract_version: str,
    artifact_summary: dict[str, Any],
    findings: list[dict[str, Any]],
    missing_evidence: list[dict[str, str]],
    validated_artifacts: list[str],
    overall_verdict: str | None = None,
    confidence: str = "medium",
    notes: str | None = None,
) -> dict[str, Any]:
    verdict = overall_verdict or compute_overall_verdict(findings, False, False)
    if verdict not in VALID_VERDICTS:
        verdict = "inconclusive"
    if verdict == "pass":
        action = "Return the Validation Report as passing evidence for Architect or Planner judgment."
    elif verdict == "fail":
        action = "Return the failed findings to the caller for scoped remediation; do not auto-repair."
    elif verdict == "blocked":
        action = "Provide the missing artifact or unblock the unreadable evidence, then rerun validation."
    elif verdict == "human_review_required":
        action = "Escalate to human review; the Validator report is evidence only and does not decide lifecycle."
    else:
        action = "Collect the missing evidence identified in the report, then rerun validation."
    return {
        "validator_role": "validator",
        "contract_id": contract_id,
        "contract_version": contract_version,
        "overall_verdict": verdict,
        "confidence": confidence,
        "artifact_summary": artifact_summary,
        "findings": findings,
        "missing_evidence": missing_evidence,
        "recommended_next_action": action,
        "validated_artifacts": sorted(validated_artifacts),
        "commands_run": [],
        "notes": notes,
    }


def unsupported_report(
    contract_id: str,
    contract_version: str,
    artifact_summary: dict[str, Any],
    validated_artifacts: list[str],
    reason: str,
) -> dict[str, Any]:
    return make_report(
        contract_id,
        contract_version,
        artifact_summary,
        [
            make_finding(
                "unsupported_contract",
                "error",
                "inconclusive",
                reason,
                "This reference implementation currently supports source/derived/contract field-mapping validation only.",
            )
        ],
        [{"expected": "source, derived, and field mapping contract artifacts", "impact": reason}],
        validated_artifacts,
        overall_verdict="human_review_required",
        confidence="medium",
        notes="Planner closure and release readiness inputs are intentionally reported as unsupported/human_review_required by this minimal reference implementation.",
    )


def run_validator(input_path: pathlib.Path) -> dict[str, Any]:
    base_dir = pathlib.Path.cwd().resolve()
    input_payload = load_json(input_path)
    if not isinstance(input_payload, dict):
        raise ValidatorError("validator input must be a JSON object")

    artifact_entries = input_payload.get("artifacts")
    if not isinstance(artifact_entries, list) or not artifact_entries:
        raise ValidatorError("validator input must contain a non-empty artifacts array")

    artifacts: dict[str, dict[str, Any]] = {}
    artifact_summary: dict[str, Any] = {}
    findings: list[dict[str, Any]] = []
    missing_evidence: list[dict[str, str]] = []
    validated_artifacts: list[str] = []

    for index, entry in enumerate(artifact_entries):
        if not isinstance(entry, dict):
            raise ValidatorError(f"artifacts[{index}] must be an object")
        path_value = entry.get("path")
        kind = entry.get("kind")
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValidatorError(f"artifacts[{index}].path must be a non-empty string")
        if not isinstance(kind, str) or not kind.strip():
            raise ValidatorError(f"artifacts[{index}].kind must be a non-empty string")
        artifact_path = resolve_path(path_value, base_dir)
        display = display_path(artifact_path, base_dir)
        try:
            payload = load_json(artifact_path)
        except ValidatorError as exc:
            artifact_summary[display] = {"kind": kind, "status": "blocked", "error": str(exc)}
            findings.append(make_finding(
                "artifact_read",
                "error",
                "blocked",
                "Referenced artifact could not be read.",
                f"{display}: {exc}",
            ))
            missing_evidence.append({"expected": display, "impact": "Validation cannot read a referenced artifact."})
            continue
        artifacts[display] = {"kind": kind, "payload": payload}
        artifact_summary[display] = artifact_summary_entry(kind, display, payload)
        validated_artifacts.append(display)

    if findings:
        return make_report(
            "artifact-read",
            "0.7.0",
            artifact_summary,
            findings,
            missing_evidence,
            validated_artifacts,
            overall_verdict="blocked",
            confidence="high",
            notes="Validation stopped because one or more referenced artifacts could not be read.",
        )

    contract, contract_id, contract_version = select_contract(input_payload.get("validation_contract"), artifacts)
    kinds = {item["kind"] for item in artifacts.values()}
    if contract and isinstance(contract.get("field_mappings"), list) and {"source", "derived"}.issubset(kinds):
        return validate_source_to_derived(
            input_payload,
            artifacts,
            contract,
            contract_id,
            contract_version,
            artifact_summary,
        )

    return unsupported_report(
        contract_id,
        contract_version,
        artifact_summary,
        validated_artifacts,
        "The input does not provide the source, derived, and field mapping contract artifacts required for executable field-mapping validation.",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Railyard dev-time Validator reference implementation.")
    parser.add_argument("--input", required=True, help="Path to a Validator input JSON file.")
    parser.add_argument("--output", help="Optional path for the Validation Report JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = pathlib.Path(args.input).resolve()
    try:
        report = run_validator(input_path)
    except ValidatorError as exc:
        report = make_report(
            "validator-input",
            "0.7.0",
            {},
            [make_finding("validator_input", "error", "blocked", str(exc), str(input_path))],
            [{"expected": "valid Validator input JSON", "impact": "Validation could not start."}],
            [],
            overall_verdict="blocked",
            confidence="high",
            notes="Input loading failed before artifact validation.",
        )
    output_text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        pathlib.Path(args.output).write_text(output_text + "\n", encoding="utf-8")
    print(output_text)
    return 0 if report["overall_verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
