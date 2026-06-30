# Semantic Calibration Fixtures

These fixtures are generic reference artifacts for the v0.7.4 semantic
validation contract. They show how semantic claims are represented for
calibration, documentation, and review examples.

The fixture set covers four semantic primitives:

- `coherence`: checks whether related artifacts make mutually consistent
  claims.
- `contradiction`: checks whether related artifacts make incompatible claims.
- `completeness`: checks whether required semantic concepts are present.
- `plausibility`: checks whether a claim is credible within the provided
  evidence and declared scope.

Each primitive is calibrated across four fixed evidence states:

- enough evidence
- missing evidence
- conflicting evidence
- unsupported semantic claim

The JSON files use the contract values `enough_evidence`,
`missing_evidence`, `conflicting_evidence`, and
`unsupported_semantic_claim`.

These files are calibration and reference artifacts only. They are not
production validation data, runtime gates, model prompts, repair plans, or
workflow orchestration inputs.

`scripts/validate_artifacts.py` validates semantic fixture shape, including
required fields, allowed primitive names, allowed evidence states, and expected
verdict/status branches. It does not execute semantic inference, evaluate
claim truth, route models, repair artifacts, or act as independent Validator
role evidence.
