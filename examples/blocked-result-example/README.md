# Blocked Result Example

This directory contains a public-safe, path-neutral example of a Runner result
file where `runner_status` is `"blocked"` or `"partial"`.

## Purpose

Demonstrates the expected structure for reporting blocked or partial results:

- **runner_status**: `"blocked"` or `"partial"`
- **notes**: A clear description of the next action
- **evidence**: Concrete strings (command outputs, file paths, logs) justifying the blocker
- **protocol_reads**: Evidence that the Runner read the Railyard role/startup contract

## Example File

- `b-example.json` - a sample blocked result showing a missing dependency blocker

## Validation

This example is public-safe (no internal paths, secrets, or credentials) and path-neutral.
It should validate with `validate_artifacts.py` when run against it.
