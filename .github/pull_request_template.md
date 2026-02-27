## Lane declaration
- Lane: <!-- platform | logic | qa-contract | observer -->
- Branch: <!-- agent/<lane>/<ticket>-<slug> -->
- Ticket: <!-- required work item reference -->

## Ownership scope touched
- Owned directories touched in this PR:
  - [ ] `engine/`
  - [ ] `fb/`
  - [ ] `ui/`
  - [ ] `pages/`
  - [ ] `plugins/`
  - [ ] `tests/`
  - [ ] `web/`
  - [ ] `scripts/`
  - [ ] `docs/`
  - [ ] `.github/`
- Additional scope notes:
  - <!-- list exact paths that crossed ownership boundaries -->

## Contract impact
- [ ] This PR changes a contract/interface consumed by another lane (`contract-impact`).
- If checked, include:
  - impacted lane(s): <!-- platform/logic/qa-contract/observer -->
  - contract delta summary:
  - required downstream handoff artifacts published:

## Required fixture/test evidence
- Fixtures added/updated:
  - <!-- file list or N/A -->
- Evidence commands + output summary:
  - `<!-- command -->`
  - `<!-- command -->`
- Deterministic acceptance criteria validated:
  - [ ] yes
  - [ ] no (explain)

## PR sizing guardrails
- Estimated lines changed: <!-- number -->
- Touched top-level directories: <!-- number -->
- [ ] Within lane soft/hard limits from `docs/parallel_dev_playbook.md`.
- [ ] If over limit, split plan or exception rationale included.

## Handoff protocol artifacts
- Upstream artifacts consumed (if dependent work):
  - <!-- links -->
- Artifacts published for downstream lanes:
  - <!-- links -->


## Governance gate verification
- [ ] CODEOWNER reviewers requested.
- [ ] All touched lane owners approved.
