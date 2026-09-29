# Changelog

## 2.2.0 — 2026-07-30

- Added a mandatory fingerprinted user-inspection gate immediately before pull-request delivery in both skill variants.
- Added the durable `awaiting-user-pr-approval` state; initial invocation, silence, generic acknowledgement, or approval of an older candidate no longer authorizes PR creation or update.
- The inspection packet now includes delivery topology, exact base/head refs and SHAs, commits, changed files, diff summary, verification failures and blind spots, risks, rollback, proposed title/body, draft state, and a candidate fingerprint.
- Any change to the candidate invalidates approval and requires a fresh inspection; rejected candidates remain unopened and may be revised or downgraded to branch-only/local delivery.
- Updated documentation, examples, validation requirements, version metadata, and package checksums.

## 2.1.0 — 2026-07-19

- Added `feature_policy=proactive` and made it the default for both OMX and standalone variants.
- Changed repository-wide runs to originate and implement verified repository-aligned features instead of limiting additions to behavior already promised by accepted sources.
- Added feature evidence requirements, acceptance contracts, compatibility rules for existing inputs, security and privacy review, documentation, verification, rollout, and rollback gates.
- Retained `strong-evidence`, `documented`, and `off` policies as progressively narrower overrides.
- Updated validation, documentation, examples, version metadata, and package checksums for the new default.

## 2.0.0 — 2026-07-12

- Changed both skill variants from conservative local maintenance to aggressive repository transformation.
- Added `rewrite_policy=aggressive`, requiring systemic fixes to consider module, dependency, architecture, and whole-codebase replacement alternatives.
- Added `compatibility=observable-output`, allowing internal implementation to change completely only when public and externally observable behavior passes differential verification.
- Increased the default budget to 50 epochs and three consecutive quiescent scans.
- Added baseline contract capture, golden and differential testing, migration-debris scans, and rewrite-specific review gates.
- Added automatic dedicated-branch push and pull-request creation through `delivery=pull-request` with `pr_state=ready` by default.
- Documented that aggressive transformations increase contract-capture, migration, regression, and review-surface risk; large replacements require stronger evidence, rollback, review, and verification rather than reduced safeguards.
- Preserved prohibitions on force push, default-branch push, automatic merge, deployment, release, secret disclosure, unrelated-work overwrite, and test weakening.
- Regenerated `CHECKSUMS.txt` for the 2.0.0 package and review-driven documentation clarifications.

## 1.2.0 — 2026-07-10

- Added `autonomous-maintainer-standalone`, a framework-independent Codex variant that has no external orchestration-skill dependency.
- Added safe `omx` and `standalone` variant selection to the POSIX and PowerShell installers and uninstallers while preserving the existing default.
- Added structural independence checks, Linux and Windows installer smoke coverage, CI validation, documentation, and invocation examples for both variants.

## 1.1.0 — 2026-07-03

- Changed the default profile to aggressive repository-wide apply mode with verified local checkpoint commits.
- Increased the default maintenance budget to 25 epochs and reduced convergence to two consecutive clean full-scope scans.
- Added a ready-to-copy Linux OMX launch command and an explicit equivalent invocation.
- Preserved the no-push, no-merge, no-deploy, no-release, and unrelated-user-work safety boundaries.

## 1.0.0 — 2026-07-02

- Added the autonomous-maintainer OMX skill.
- Added safe user/project installers for POSIX shells and PowerShell.
- Added uninstallers, structural validation, installer smoke tests, CI, examples, and documentation.
