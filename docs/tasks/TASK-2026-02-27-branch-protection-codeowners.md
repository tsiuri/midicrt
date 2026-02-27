# TASK-2026-02-27-branch-protection-codeowners

## Goal
Apply repository branch protection on `master` with CODEOWNER review required.

## Configuration source
- `.github/branch-protection/master.json`

## Apply command
```bash
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/tsiuri/midicrt/branches/master/protection" \
  --input .github/branch-protection/master.json
```

## Verification command
```bash
gh api \
  -H "Accept: application/vnd.github+json" \
  "/repos/tsiuri/midicrt/branches/master/protection"
```
