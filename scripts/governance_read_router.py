#!/usr/bin/env python3
"""Deterministic governance read resolver.

Hard constraints:
- Python standard library only (no third-party imports)
- Read-only: only reads files from disk, never writes
- Deterministic: same input always produces same output
- Fail-closed: unknown inputs -> blocked, never permissive defaults
- Never mutate caller input
- No fuzzy/prose/filesystem/model inference for resolution
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class GovernanceRoutingConfigurationError(Exception):
    """Raised when the governance routing configuration is invalid."""
    pass


# ---------------------------------------------------------------------------
# Valid enums and known request fields
# ---------------------------------------------------------------------------

VALID_ROLES = frozenset({"planner", "architect", "runner", "validator", "knowledge_curator"})
VALID_CONDITIONS = frozenset({"validator_required", "epic_closure", "validation_task",
                               "governance_task", "knowledge_task", "runtime_task"})
VALID_TOPICS = frozenset({"semantic"})
VALID_CONTRACT_REF_FORMS = frozenset({"path", "document_id", "canonical_for"})
VALID_GUIDE_REF_FORMS = frozenset({"path", "document_id"})
NORMATIVE_KINDS = frozenset({"protocol", "policy", "contract", "registry", "schema"})
VALID_GOVERNANCE_KINDS = frozenset({"protocol", "policy", "contract", "schema", "registry", "guide"})
VALID_STATUSES = frozenset({"active", "superseded", "inactive"})
VALID_AUTHORITY_LEVELS = frozenset({"canonical", "normative_reference", "informational"})
VALID_OVERRIDEABILITIES = frozenset({"non_overridable", "stricter_only", "informational"})
REQUIRED_METADATA_FIELDS = frozenset({
    "document_id", "governance_kind", "version", "authority_level",
    "owner", "scope", "applies_to", "overrideability", "status",
})

KNOWN_REQUEST_FIELDS = frozenset({
    "role",
    "validator_required",
    "epic_closure",
    "validation_task",
    "validation_topic",
    "governance_task",
    "knowledge_task",
    "runtime_task",
    "explicit_contract_refs",
    "explicit_guide_refs",
})

BOOLEAN_FIELDS = frozenset({
    "validator_required", "epic_closure", "validation_task",
    "governance_task", "knowledge_task", "runtime_task",
})


# ---------------------------------------------------------------------------
# Resolver defaults
# ---------------------------------------------------------------------------

def _default_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------

def validate_governance_configuration(railyard_root):
    """Validate governance inventory, routing registry, and taxonomy.

    Returns None on success.
    Raises GovernanceRoutingConfigurationError on any configuration error.
    """
    railyard_root = pathlib.Path(railyard_root)

    # 1. Inventory file exists and is valid JSON
    inventory_path = railyard_root / "references" / "governance-document-inventory.json"
    if not inventory_path.is_file():
        raise GovernanceRoutingConfigurationError(
            f"Governance document inventory not found: {inventory_path}")
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GovernanceRoutingConfigurationError(
            f"Governance document inventory is not valid JSON: {exc}")

    documents = inventory.get("documents", [])
    exclusions = inventory.get("exclusions", [])

    # 2. Routing registry exists and is valid JSON
    routing_path = railyard_root / "references" / "governance-read-routing.json"
    if not routing_path.is_file():
        raise GovernanceRoutingConfigurationError(
            f"Governance read-routing registry not found: {routing_path}")
    try:
        routing = json.loads(routing_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GovernanceRoutingConfigurationError(
            f"Governance read-routing registry is not valid JSON: {exc}")

    # 3. Taxonomy file exists
    taxonomy_path = railyard_root / "references" / "governance-document-taxonomy.md"
    if not taxonomy_path.is_file():
        raise GovernanceRoutingConfigurationError(
            f"Governance document taxonomy not found: {taxonomy_path}")

    # Build lookup maps
    paths_set: set[str] = set()
    doc_ids_set: set[str] = set()
    active_canonical: dict[str, list[str]] = {}  # canonical_for_value -> list of document_ids
    supersedes_graph: dict[str, list[str]] = {}   # document_id -> list of superseded doc_ids
    doc_by_path: dict[str, dict] = {}
    doc_by_id: dict[str, dict] = {}
    doc_by_canonical: dict[str, list[dict]] = {}
    all_doc_ids: set[str] = set()

    for doc in documents:
        path_val = doc.get("path", "")
        meta = doc.get("metadata", {})
        doc_id = meta.get("document_id", "")
        kind = meta.get("governance_kind", "")
        status = meta.get("status", "")
        supersedes = meta.get("supersedes", [])
        canonical_for = meta.get("canonical_for", [])
        mixed_sections = doc.get("mixed_sections", [])
        canonical_links = doc.get("canonical_links", [])

        # 4. Every inventory path exists on disk
        full_path = railyard_root / path_val
        if not full_path.exists():
            raise GovernanceRoutingConfigurationError(
                f"Governance inventory path does not exist on disk: {path_val}")

        # 5. No duplicate path
        if path_val in paths_set:
            raise GovernanceRoutingConfigurationError(
                f"Duplicate path in governance inventory: {path_val}")
        paths_set.add(path_val)

        # 6. No duplicate document_id
        if doc_id in doc_ids_set:
            raise GovernanceRoutingConfigurationError(
                f"Duplicate document_id in governance inventory: {doc_id}")
        doc_ids_set.add(doc_id)
        all_doc_ids.add(doc_id)

        # 7. Validate required metadata fields are present
        missing_fields = sorted(REQUIRED_METADATA_FIELDS - set(meta.keys()))
        if missing_fields:
            raise GovernanceRoutingConfigurationError(
                f"Missing required metadata field(s) for document {path_val}: "
                f"{', '.join(missing_fields)}")

        # 8. Validate governance_kind enum
        if kind not in VALID_GOVERNANCE_KINDS:
            raise GovernanceRoutingConfigurationError(
                f"Invalid governance_kind '{kind}' for document {path_val}; "
                f"must be one of: {sorted(VALID_GOVERNANCE_KINDS)}")

        # 9. Validate status enum
        if status not in VALID_STATUSES:
            raise GovernanceRoutingConfigurationError(
                f"Invalid status '{status}' for document {path_val}; "
                f"must be one of: {sorted(VALID_STATUSES)}")

        # 10. Validate authority_level enum
        authority_level = meta.get("authority_level", "")
        if authority_level not in VALID_AUTHORITY_LEVELS:
            raise GovernanceRoutingConfigurationError(
                f"Invalid authority_level '{authority_level}' for document {path_val}; "
                f"must be one of: {sorted(VALID_AUTHORITY_LEVELS)}")

        # 11. Validate overrideability enum
        overrideability = meta.get("overrideability", "")
        if overrideability not in VALID_OVERRIDEABILITIES:
            raise GovernanceRoutingConfigurationError(
                f"Invalid overrideability '{overrideability}' for document {path_val}; "
                f"must be one of: {sorted(VALID_OVERRIDEABILITIES)}")

        # 12. Cross-field taxonomy rules
        # governance_kind: guide requires authority_level: informational and overrideability: informational
        if kind == "guide":
            if authority_level != "informational":
                raise GovernanceRoutingConfigurationError(
                    f"Guide document '{path_val}' must have authority_level: 'informational', "
                    f"got '{authority_level}'")
            if overrideability != "informational":
                raise GovernanceRoutingConfigurationError(
                    f"Guide document '{path_val}' must have overrideability: 'informational', "
                    f"got '{overrideability}'")

        # overrideability: informational requires authority_level: informational
        if overrideability == "informational" and authority_level != "informational":
            raise GovernanceRoutingConfigurationError(
                f"Document '{path_val}' with overrideability 'informational' must have "
                f"authority_level 'informational', got '{authority_level}'")

        # Non-informational docs must declare non-empty canonical_for
        if authority_level != "informational" and not canonical_for:
            raise GovernanceRoutingConfigurationError(
                f"Non-informational document '{path_val}' must declare non-empty canonical_for")

        # Informational/Guide docs must NOT declare canonical_for
        if authority_level == "informational" and canonical_for:
            raise GovernanceRoutingConfigurationError(
                f"Informational document '{path_val}' must NOT declare canonical_for")

        doc_by_path[path_val] = doc
        doc_by_id[doc_id] = doc

        # 7. No duplicate active canonical_for values
        if status == "active":
            for cf in canonical_for:
                active_canonical.setdefault(cf, []).append(doc_id)

        # Collect canonical_for for later lookup
        for cf in canonical_for:
            doc_by_canonical.setdefault(cf, []).append(doc)

        # 8. No self-supersedes
        if doc_id in supersedes:
            raise GovernanceRoutingConfigurationError(
                f"Self-supersedes detected: {doc_id} supersedes itself")

        supersedes_graph[doc_id] = supersedes if isinstance(supersedes, list) else []

        # 11. No mixed-section canonical links to non-existent documents
        for ms in mixed_sections:
            target = ms.get("canonical_target", "")
            target_full = railyard_root / target
            if not target_full.exists():
                raise GovernanceRoutingConfigurationError(
                    f"Mixed-section canonical target does not exist: {target} "
                    f"(from {path_val}, heading: {ms.get('heading', 'unknown')})")

        for cl in canonical_links:
            target = cl.get("canonical_document", "")
            target_full = railyard_root / target
            if not target_full.exists():
                raise GovernanceRoutingConfigurationError(
                    f"Canonical link target does not exist: {target} (from {path_val})")

    # 7. Check duplicate active canonical_for
    for cf, ids in active_canonical.items():
        if len(ids) > 1:
            raise GovernanceRoutingConfigurationError(
                f"Duplicate active canonical_for '{cf}': {ids}")

    # 9. No broken supersedes
    for doc_id, supers in supersedes_graph.items():
        for sup_id in supers:
            if sup_id not in all_doc_ids:
                raise GovernanceRoutingConfigurationError(
                    f"Broken supersedes: {doc_id} supersedes non-existent {sup_id}")

    # 10. No cyclic supersedes
    def _has_cycle(supers_graph):
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in supers_graph}

        def dfs(node):
            color[node] = GRAY
            for neighbor in supers_graph.get(node, []):
                if color.get(neighbor, WHITE) == GRAY:
                    return True
                if color.get(neighbor, WHITE) == WHITE:
                    if dfs(neighbor):
                        return True
            color[node] = BLACK
            return False

        for node in supers_graph:
            if color.get(node, WHITE) == WHITE:
                if dfs(node):
                    return True
        return False

    if _has_cycle(supersedes_graph):
        raise GovernanceRoutingConfigurationError("Cyclic supersedes detected in inventory")

    # 12. No document path matching exclusion pattern
    for doc in documents:
        path_val = doc.get("path", "")
        for excl in exclusions:
            pattern = excl.get("pattern", "")
            if pattern.endswith("/"):
                if path_val.startswith(pattern):
                    raise GovernanceRoutingConfigurationError(
                        f"Governance document path matches exclusion pattern '{pattern}': {path_val}")
            else:
                if path_val == pattern:
                    raise GovernanceRoutingConfigurationError(
                        f"Governance document path matches exclusion pattern '{pattern}': {path_val}")

    # 13. All routing registry role names exist, required_reads paths exist
    roles = routing.get("roles", {})
    for role_name, role_data in roles.items():
        if role_name not in VALID_ROLES:
            raise GovernanceRoutingConfigurationError(
                f"Unknown role in routing registry: {role_name}")
        for read_path in role_data.get("required_reads", []):
            if read_path not in doc_by_path:
                raise GovernanceRoutingConfigurationError(
                    f"Role '{role_name}' required_read references unknown path: {read_path}")

    # 14. All routing conditional rule targets resolve to valid inventory entries
    for rule in routing.get("conditional_rules", []):
        for include_path in rule.get("action", {}).get("includes", []):
            if include_path not in doc_by_path:
                raise GovernanceRoutingConfigurationError(
                    f"Conditional rule '{rule.get('rule_id')}' includes unknown path: {include_path}")

    # 15. Routing conditional rule conditions are valid enum values
    for rule in routing.get("conditional_rules", []):
        condition = rule.get("predicate", {}).get("condition")
        if condition and condition not in VALID_CONDITIONS:
            raise GovernanceRoutingConfigurationError(
                f"Invalid condition in rule '{rule.get('rule_id')}': {condition}")

    # 16. Baseline/conditional targets must be normative and active
    def _check_baseline_target(path_val, context):
        if path_val in doc_by_path:
            meta = doc_by_path[path_val].get("metadata", {})
            kind_val = meta.get("governance_kind", "")
            status_val = meta.get("status", "")
            if kind_val == "guide":
                raise GovernanceRoutingConfigurationError(
                    f"Non-normative Guide document in {context}: {path_val}")
            if status_val != "active":
                raise GovernanceRoutingConfigurationError(
                    f"Non-active document in {context}: {path_val} (status='{status_val}')")

    for role_name, role_data in roles.items():
        for read_path in role_data.get("required_reads", []):
            _check_baseline_target(read_path, f"baseline required_reads of role '{role_name}'")

    for rule in routing.get("conditional_rules", []):
        for include_path in rule.get("action", {}).get("includes", []):
            _check_baseline_target(include_path, f"conditional includes of rule '{rule.get('rule_id')}'")

    # 17. Each role baseline must include at least one Protocol AND one Policy
    ROLES_WITH_DEDICATED_SET = frozenset({"validator", "knowledge_curator"})
    for role_name, role_data in roles.items():
        if role_name in ROLES_WITH_DEDICATED_SET:
            continue
        has_protocol = False
        has_policy = False
        for read_path in role_data.get("required_reads", []):
            if read_path in doc_by_path:
                kind = doc_by_path[read_path].get("metadata", {}).get("governance_kind", "")
                if kind == "protocol":
                    has_protocol = True
                elif kind == "policy":
                    has_policy = True
        if not has_protocol or not has_policy:
            missing = []
            if not has_protocol:
                missing.append("Protocol")
            if not has_policy:
                missing.append("Policy")
            raise GovernanceRoutingConfigurationError(
                f"Role '{role_name}' baseline missing: {', '.join(missing)}")


# ---------------------------------------------------------------------------
# Request validation helpers
# ---------------------------------------------------------------------------

def _validate_request(request: dict) -> dict | None:
    """Validate the request dict. Returns blocked result dict on invalidity, None if valid."""
    if not isinstance(request, dict):
        return _blocked("invalid_kind")

    # 1. Unknown fields (lexicographically first)
    unknown_fields = sorted(set(request.keys()) - KNOWN_REQUEST_FIELDS)
    if unknown_fields:
        return _blocked("unknown_field", field=unknown_fields[0])

    # 2. Missing role
    if "role" not in request:
        return _blocked("missing_role", field="role")

    role = request["role"]

    # Type check for role
    if not isinstance(role, str):
        return _blocked("invalid_kind", field="role")

    # 3. Invalid role
    if role not in VALID_ROLES:
        return _blocked("invalid_kind", field="role")

    # Type checks for boolean fields
    for field in BOOLEAN_FIELDS:
        if field in request and request[field] is not None:
            if not isinstance(request[field], bool):
                return _blocked("invalid_kind", field=field)

    # 4. validation_topic present but not "semantic"
    if "validation_topic" in request:
        vt = request["validation_topic"]
        if not isinstance(vt, str):
            return _blocked("invalid_kind", field="validation_topic")
        if vt not in VALID_TOPICS:
            return _blocked("invalid_kind", field="validation_topic")

    # 5. validation_topic present without validation_task=true
    if "validation_topic" in request:
        if not request.get("validation_task", False):
            return _blocked("invalid_kind", field="validation_topic")

    # 6. Invalid contract_ref form
    if "explicit_contract_refs" in request:
        refs = request["explicit_contract_refs"]
        if not isinstance(refs, list):
            return _blocked("invalid_kind", field="explicit_contract_refs")
        for ref in refs:
            if not isinstance(ref, dict):
                return _blocked("invalid_kind", field="explicit_contract_refs")
            form = ref.get("form", "")
            value = ref.get("value", "")
            if not isinstance(form, str) or not isinstance(value, str):
                return _blocked("invalid_kind", field="explicit_contract_refs")
            if form not in VALID_CONTRACT_REF_FORMS:
                return _blocked("invalid_form", ref=str(ref))
            if not value:
                return _blocked("invalid_form", ref=str(ref))

    # 7. Invalid guide_ref form
    if "explicit_guide_refs" in request:
        refs = request["explicit_guide_refs"]
        if not isinstance(refs, list):
            return _blocked("invalid_kind", field="explicit_guide_refs")
        for ref in refs:
            if not isinstance(ref, dict):
                return _blocked("invalid_kind", field="explicit_guide_refs")
            form = ref.get("form", "")
            value = ref.get("value", "")
            if not isinstance(form, str) or not isinstance(value, str):
                return _blocked("invalid_kind", field="explicit_guide_refs")
            if form not in VALID_GUIDE_REF_FORMS:
                return _blocked("invalid_form", ref=str(ref))
            if not value:
                return _blocked("invalid_form", ref=str(ref))

    return None


# ---------------------------------------------------------------------------
# Ref resolution
# ---------------------------------------------------------------------------

def _resolve_contract_ref(request_ref: dict, inventory_data: dict) -> str | dict:
    """Resolve a contract ref to a path string.

    Returns the resolved path string on success, or a blocked result dict on failure.
    """
    form = request_ref["form"]
    value = request_ref["value"]
    documents = inventory_data.get("documents", [])

    matches = []
    for doc in documents:
        path_val = doc.get("path", "")
        meta = doc.get("metadata", {})
        kind = meta.get("governance_kind", "")

        if form == "path":
            if path_val == value:
                matches.append(doc)
        elif form == "document_id":
            if meta.get("document_id") == value:
                matches.append(doc)
        elif form == "canonical_for":
            if value in meta.get("canonical_for", []):
                matches.append(doc)

    if not matches:
        return _blocked("unknown_ref", ref=value)

    # Separate guides from normative
    normative_matches = [d for d in matches
                         if d.get("metadata", {}).get("governance_kind", "") != "guide"]
    guide_matches = [d for d in matches
                     if d.get("metadata", {}).get("governance_kind", "") == "guide"]

    # If ALL matches are guides and it's a contract ref -> guide_only_match
    if not normative_matches and guide_matches:
        return _blocked("guide_only_match", ref=value)

    # Filter to active only
    active_matches = [d for d in normative_matches
                      if d.get("metadata", {}).get("status") == "active"]

    # Check if there were matches but none are active
    if not active_matches and normative_matches:
        return _blocked("inactive_or_superseded", ref=value)

    if not active_matches:
        return _blocked("unknown_ref", ref=value)

    if len(active_matches) > 1:
        candidates = sorted(d.get("path", "") for d in active_matches)
        return _blocked("multiple_active_matches", ref=value, candidates=candidates)

    return active_matches[0]["path"]


def _resolve_guide_ref(request_ref: dict, inventory_data: dict) -> str | dict:
    """Resolve a guide ref to a path string.

    Returns the resolved path string on success, or a blocked result dict on failure.
    """
    form = request_ref["form"]
    value = request_ref["value"]
    documents = inventory_data.get("documents", [])

    matches = []
    for doc in documents:
        path_val = doc.get("path", "")
        meta = doc.get("metadata", {})

        if form == "path":
            if path_val == value:
                matches.append(doc)
        elif form == "document_id":
            if meta.get("document_id") == value:
                matches.append(doc)

    if not matches:
        return _blocked("unknown_ref", ref=value)

    # Only Guide-kind documents can be resolved as guide refs
    guide_matches = [d for d in matches
                     if d.get("metadata", {}).get("governance_kind", "") == "guide"]

    if not guide_matches:
        # Non-Guide matched as guide -> invalid_kind
        return _blocked("invalid_kind", ref=value)

    # Filter to active only
    active_matches = [d for d in guide_matches
                      if d.get("metadata", {}).get("status") == "active"]

    if not active_matches:
        return _blocked("inactive_or_superseded", ref=value)

    if len(active_matches) > 1:
        candidates = sorted(d.get("path", "") for d in active_matches)
        return _blocked("multiple_active_matches", ref=value, candidates=candidates)

    return active_matches[0]["path"]


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

def resolve_governance_reads(request, railyard_root=None):
    """Resolve governance reads from a routing request.

    Args:
        request: dict with role, optional boolean flags, optional validation_topic,
                 optional explicit_contract_refs, optional explicit_guide_refs
        railyard_root: pathlib.Path to project root (default: script parent dir's parent)

    Returns:
        dict: routing_result JSON (status='ready' with normative_reads + supplemental_guides,
              or status='blocked' with reason + optional field/ref/candidates)

    Raises:
        GovernanceRoutingConfigurationError: on config validation failure
    """
    if railyard_root is None:
        railyard_root = _default_root()
    else:
        railyard_root = pathlib.Path(railyard_root)

    # Validate configuration
    validate_governance_configuration(railyard_root)

    # Make a shallow copy - never mutate caller input
    request = dict(request)

    # Validate request
    validation_result = _validate_request(request)
    if validation_result is not None:
        return validation_result

    role = request["role"]

    # Load routing registry
    routing_path = railyard_root / "references" / "governance-read-routing.json"
    routing = json.loads(routing_path.read_text(encoding="utf-8"))

    # Load inventory
    inventory_path = railyard_root / "references" / "governance-document-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    # Phase 1: Role baseline required_reads
    normative_reads = []
    seen = set()

    role_baseline = routing.get("roles", {}).get(role, {})
    for read_path in role_baseline.get("required_reads", []):
        if read_path not in seen:
            normative_reads.append(read_path)
            seen.add(read_path)

    # Phase 2: Matching conditional_rules
    for rule in routing.get("conditional_rules", []):
        predicate = rule.get("predicate", {})
        condition = predicate.get("condition", "")
        applies_to_roles = predicate.get("applies_to_roles", [])
        topic_required = predicate.get("topic_required")

        # Check if condition matches
        condition_matches = False
        if condition == "validation_task":
            if request.get("validation_task", False):
                # Check topic_required
                if topic_required:
                    # cond-validation-semantic: requires validation_task=true AND validation_topic=<topic>
                    if request.get("validation_topic") == topic_required:
                        condition_matches = True
                else:
                    # cond-validation-task: topic_required absent -> match on validation_task=true only
                    condition_matches = True
        else:
            # Other conditions: check the boolean flag
            flag_name = condition
            if request.get(flag_name, False):
                condition_matches = True

        # Check role applicability
        if condition_matches:
            if not applies_to_roles or role in applies_to_roles:
                for include_path in rule.get("action", {}).get("includes", []):
                    if include_path not in seen:
                        normative_reads.append(include_path)
                        seen.add(include_path)

    # Phase 3: Explicit contract_refs
    for ref in request.get("explicit_contract_refs", []):
        resolved = _resolve_contract_ref(ref, inventory)
        if isinstance(resolved, dict):
            return resolved  # blocked
        if resolved not in seen:
            normative_reads.append(resolved)
            seen.add(resolved)

    # Phase 4: Explicit guide_refs -> supplemental_guides
    supplemental_guides = []
    for ref in request.get("explicit_guide_refs", []):
        resolved = _resolve_guide_ref(ref, inventory)
        if isinstance(resolved, dict):
            return resolved  # blocked
        if resolved not in supplemental_guides:
            supplemental_guides.append(resolved)

    return {
        "status": "ready",
        "role": role,
        "normative_reads": normative_reads,
        "supplemental_guides": supplemental_guides,
    }


# ---------------------------------------------------------------------------
# Blocked result helpers
# ---------------------------------------------------------------------------

def _blocked(reason: str, **kwargs) -> dict:
    result: dict = {"status": "blocked", "reason": reason}
    result.update({k: v for k, v in kwargs.items() if v is not None})
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_ref_arg(value: str, forms: frozenset) -> dict:
    """Parse a key=value ref argument into {form, value}."""
    if "=" not in value:
        raise ValueError(f"Invalid ref form: {value}")
    key, val = value.split("=", 1)
    if key not in {"path", "document_id", "canonical_for"}:
        raise ValueError(f"Invalid ref form: unsupported form '{key}'")
    if key == "canonical_for" and "canonical_for" not in forms:
        raise ValueError(f"Invalid ref form: unsupported form '{key}'")
    return {"form": key, "value": val}


def _build_request(args: argparse.Namespace, railyard_root: pathlib.Path) -> dict:
    """Build request dict from CLI args with schema validation."""
    request = {"role": args.role}

    boolean_flags = {
        "validator_required": args.validator_required,
        "epic_closure": args.epic_closure,
        "validation_task": args.validation_task,
        "governance_task": args.governance_task,
        "knowledge_task": args.knowledge_task,
        "runtime_task": args.runtime_task,
    }
    for flag_name, flag_value in boolean_flags.items():
        if flag_value:
            request[flag_name] = True

    if args.validation_topic:
        request["validation_topic"] = args.validation_topic

    if args.contract_ref:
        refs = []
        for ref_str in args.contract_ref:
            refs.append(_parse_ref_arg(ref_str, VALID_CONTRACT_REF_FORMS))
        request["explicit_contract_refs"] = refs

    if args.guide_ref:
        refs = []
        for ref_str in args.guide_ref:
            refs.append(_parse_ref_arg(ref_str, VALID_GUIDE_REF_FORMS))
        request["explicit_guide_refs"] = refs

    return request


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic governance read resolver")
    parser.add_argument("--role", required=True, help="Role name")
    parser.add_argument("--validator-required", action="store_true", help="Validator required flag")
    parser.add_argument("--epic-closure", action="store_true", help="Epic closure flag")
    parser.add_argument("--validation-task", action="store_true", help="Validation task flag")
    parser.add_argument("--governance-task", action="store_true", help="Governance task flag")
    parser.add_argument("--knowledge-task", action="store_true", help="Knowledge task flag")
    parser.add_argument("--runtime-task", action="store_true", help="Runtime task flag")
    parser.add_argument("--validation-topic", help="Validation topic (e.g., semantic)")
    parser.add_argument("--contract-ref", action="append", default=[],
                        help="Contract ref (key=value)")
    parser.add_argument("--guide-ref", action="append", default=[],
                        help="Guide ref (key=value)")
    parser.add_argument("--railyard-root", default=None,
                        help="Project root path")

    args = parser.parse_args()
    railyard_root = pathlib.Path(args.railyard_root) if args.railyard_root else _default_root()

    try:
        request = _build_request(args, railyard_root)
    except ValueError as exc:
        # Request validation errors are blocked requests, not config errors
        error_msg = str(exc)
        if "invalid ref form" in error_msg.lower() or "unsupported form" in error_msg.lower():
            reason = "invalid_form"
        elif "validation_topic" in error_msg.lower():
            reason = "invalid_kind"
        else:
            reason = "invalid_kind"
        result = {"status": "blocked", "reason": reason}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    try:
        result = resolve_governance_reads(request, railyard_root)
    except GovernanceRoutingConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("status") == "ready":
        return 0
    elif result.get("status") == "blocked":
        return 2
    else:
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
