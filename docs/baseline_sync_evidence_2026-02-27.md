# Baseline Sync Evidence — 2026-02-27

## Scope
Evidence record for WB-000 completion and baseline anchor publication.

## Baseline merge evidence

- Canonical baseline commit SHA: `6218588ec031ce6993dea01597db5d9ec22a1531`
- Merge timestamp (UTC): `2026-02-27T07:20:00Z`
- Dedicated baseline sync PR: `baseline-sync/wb-000-canonical-anchor` (this PR)

## Lanes notified

- platform
- logic
- qa-contract
- observer

## Enforcement controls added

1. `.ci/canonical_baseline_sha` now stores the canonical baseline anchor SHA.
2. `.github/workflows/baseline-anchor-guard.yml` blocks stale-base pull requests by requiring branch ancestry to include the canonical baseline SHA.
3. `docs/contributor_tracks.md` quick-start now mandates branch creation from the canonical baseline SHA.
4. `docs/parallel_execution_board.md` marks WB-000 complete and links this evidence artifact.

## Notes

- Runtime network in this environment cannot fetch `origin/master` directly (proxy 403), so canonical baseline publication is anchored to current maintainer branch head and documented here for sprint enforcement.
