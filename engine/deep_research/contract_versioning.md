# Deep Research Contract Versioning

This document defines staged-change rules for `ResearchContract`.

## Version format

- Contract versions use `MAJOR.MINOR` (for example: `1.0`).
- `MAJOR` changes indicate potentially breaking shape changes.
- `MINOR` changes indicate additive evolution only.

## Compatibility policy

- Additive fields are allowed in minor-stage updates.
  - Examples: adding optional top-level contract keys, adding optional nested fields.
  - Existing required fields and value semantics must remain stable.
- Breaking shape changes require a staged rollout.
  - Examples: removing/renaming required fields, changing required field types, changing incompatible payload structure.

## Required staged rollout for breaking changes

1. **Prepare readers first**
   - Land compatibility readers that support both the old and new shapes.
   - Keep writers on the old shape.
2. **Dual-read validation phase**
   - Run in production with mixed agents/versions and validate deterministic behavior.
   - Ensure tests cover old writer/new reader and new writer/new reader paths.
3. **Writer switch phase**
   - Flip writers to emit the new major contract only after all readers are upgraded.
4. **Cleanup phase**
   - Remove old-shape compatibility code in a later follow-up after stability window.

## Parallel-agent safety

When multiple agents may run concurrently, always assume temporary version skew.
Use major-version compatibility checks to fail fast with deterministic error payloads,
rather than partially parsing unknown contract shapes.
