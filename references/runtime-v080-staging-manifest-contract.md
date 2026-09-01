# Runtime v0.8 Staging Manifest Contract

**Document ID**: railyard-runtime-v080-staging-manifest-contract-v2.0.0

**Version**: 2.0.0

## 1. Authority and history

This is the sole public declarative authority for a Runtime v0.8 staging manifest. It decides explicitly named repository files only. It grants no copy, filesystem, command, environment, Git, CI, staging, commit, tag, push, publication, or release authority.

Version 2.0.0 supersedes v1.0.0. The v1.1.0 candidate is rejected, non-authoritative history. The v1 schema and v1 conformance catalog remain preserved historical candidates and are excluded from final distribution authority.

## 2. Public value model

`StagingManifest` is closed. There are no extension fields or arbitrary public text fields. Every manifest value is a closed enum, `SafePath`, `PublicHandle`, non-negative integer, boolean, null, or lowercase 64-hex digest.

`PublicHandle` is a bounded public slug or `urn:railyard:staging:` handle; it is not a ticket, epic, person, lifecycle, verdict, URL, command, environment, or location identifier. A digest is opaque and never self-asserts acceptance.

`SafePath` is reused by every path-bearing field. It is repository-relative POSIX regular-file form and fails closed for absolute, drive, URI, backslash, empty, dot, glob, whitespace, control, and case-normalized protected forms. Protected forms include Control, mailbox, workflow, evidence, report, tool state, cache, temporary, secret, credential, local configuration, generated output, distribution, and directory namespaces. Matching is segment-exact: `references/credentials-notes.md` remains safe.

## 3. Closed envelope

The manifest has exactly `manifest_version` (`2.0.0`), `release_target` (`v0.8.0`), `manifest_id`, `external_audit_digest_or_null`, `ready_to_stage`, `entries`, and `totals`.

No lifecycle, ticket, epic, owner identity, verdict prose, evidence path, Control path, authorization, command, environment, provenance assertion, schema reference, contract reference, timestamp, or extension field is permitted.

## 4. Entry union

Every entry has one `SafePath` and one decision: `include`, `exclude`, `defer`, or `remediate`. Paths are unique and byte-wise ascending.

An include has immutable `sha256`, `size_bytes`, `line_count_or_null`, `category`, `purpose_code`, `provenance_digests`, and `source_or_derived`. Categories are `contract`, `schema`, `registry`, `guide`, `policy`, `protocol`, `reference`, `example`, `script`, `test`, or `documentation`. Purpose codes are `runtime_implementation`, `governance_authority`, `structural_schema`, `conformance_fixture`, `assurance_test`, `public_documentation`, `project_configuration`, `license`, or `release_metadata`. A source include forbids `derivation`; a derived include requires it.

Exclude codes are `out_of_scope`, `test_fixture`, `local_configuration`, `secret`, `machine_specific`, `temporary`, `cache`, `control_state`, `generated_unproven`, or `policy_excluded`. Defer codes are `pending_provenance`, `pending_decision`, `pending_derivation`, or `pending_review`. They contain no rationale text.

Remediate codes are `needs_provenance`, `needs_derivation`, `needs_cleanup`, `needs_reclassification`, or `needs_signoff`. `RemediationAction` is text-free: `action_kind`, `owner_class`, exactly one `target_path` or `target_artifact_digest`, and `completion_rule`. Action kinds are `obtain_provenance_digest`, `record_derivation`, `remove_forbidden_content`, `reclassify_content`, or `request_external_audit`; owner classes are `artifact_steward`, `governance_steward`, `maintenance_steward`, or `release_steward`; completion rules are `digest_resolved`, `derivation_registered`, `content_reclassified`, `path_removed`, or `audit_digest_confirmed`.

## 5. Derived entries and totals

`Derivation` is closed: `generator_path`, `generator_sha256`, ordered non-empty `input_path_bindings`, `output_verification_rule`, and `rebuild_policy`. A binding has exactly `path` and `sha256`. Verification rules are `immutable_sha256_match`, `byte_equality`, or `content_equality`; rebuild policies are `rebuild_when_inputs_change`, `rebuild_per_manifest`, or `rebuild_prohibited`. Generator discovery, command text, environment lookup, implicit input, and unbound input are forbidden.

`Totals` is closed and authored: `entry_count`, closed `decision_counts`, `bytes_total`, `lines_total`, `line_count_unknown`, closed `composition`, and closed `generated_counts`. Decision counts match entries and sum to entry count. Include bindings reconcile bytes and lines; a null line count contributes zero lines and increments unknown. Assurance categories are `contract`, `schema`, `policy`, `protocol`, `registry`, and `test`; all others are implementation. Source plus derived equals include count.

`ready_to_stage=true` requires an include, no remediation, reconciled paths/bindings/totals, resolvable include provenance, and a non-null confirmed audit digest. It remains declarative only.

## 6. Executable fixture context

`examples/runtime_v080_staging_manifest/conformance-v2.json` contains closed fixture-local resolver contexts with neutral `regular_files`, `resolvable_provenance_digests`, `confirmed_external_audit_digests`, `registered_generators`, `prior_deferred_paths`, and `protected_classifications`. It contains no Control identifier/path, lifecycle assertion, acceptance assertion, or external resolver dependency.

Each case identifies one complete candidate or base candidate plus explicit ordered RFC 6901 operations, exactly one context, structural outcome, and exactly one structural failure or semantic code if invalid. Materialization starts by copying the named base and fails if an operation pointer cannot resolve.

## 7. Semantic order

After Draft 2020-12 validation, the semantic oracle returns the first matching code only:

1. `path_collision`
2. `path_order_invalid`
3. `path_resolution_failed`
4. `content_binding_mismatch`
5. `provenance_digest_unresolved`
6. `audit_digest_unresolved`
7. `derivation_invalid`
8. `totals_mismatch`
9. `ready_to_stage_invalid`
10. `deferred_path_conflict`
11. `protected_content`
12. `non_declarative_authority`

The catalog supplies a reachable single-cause case for each applicable semantic code, executes all rules in every positive case, and declares precedence collisions. `path_resolution_failed` covers missing selected regular files or derivation inputs; `content_binding_mismatch` covers selected-file digest, size, or line disagreement; `non_declarative_authority` covers a fixture interpretation requesting an operation.

## 8. Validation and distribution boundary

The independent Validator materializes the catalog without using expected outcomes as truth, builds its own oracle from this contract, runs every fixture and off-catalog mutation, and proves the exact five-file scope against the Control pre-edit byte snapshots.

The inventory registration points only to `assets/schemas/runtime-v080-staging-manifest-v2.schema.json`. The v2 schema is active/canonical; the v1 schema is superseded and byte-identical. This contract, schema, and catalog create no manifest instance, validator executable, copy builder, CI, audit, or release operation.
