# Ticket Validator Gate Examples

These public-safe ticket fixtures demonstrate the two valid gate decisions:

- `DOMAIN-EXAMPLE-VALIDATOR-001.md` records that independent Validator evidence
  is not required and explains why.
- `SYSTEM-EXAMPLE-VALIDATOR-001.md` records a required Validator gate with the
  metadata needed to construct a later Validator dispatch.

Run artifact-shape validation with:

```powershell
python scripts/validate_artifacts.py --project-root .
```

That command checks the ticket metadata shape. It does not execute an
independent Validator role and does not satisfy the required gate in the System
example.
