# Autonomous Maintainer Skills for Codex

Two repository-wide maintenance skills that proactively add verified, repository-aligned features by default, aggressively discover and apply other verifiable improvements, allow large refactors or complete rewrites when accepted observable behavior remains equivalent, and require user inspection and explicit approval immediately before dedicated-branch pull-request delivery.

| Variant | Skill | Runtime |
|---|---|---|
| Standalone | `autonomous-maintainer-standalone` | Codex, Git, and repository tools |
| OMX | `autonomous-maintainer` | Codex plus Oh My Codex |

## New default behavior in 2.2

The default profile now uses:

```text
mode=apply
focus=all
feature_policy=proactive
resume=true
commit=checkpoint
max_epochs=50
quiescence_scans=3
parallelism=auto
network=public-read
rewrite_policy=aggressive
compatibility=observable-output
delivery=pull-request
pr_state=ready
```

“Aggressive” means exhaustive search and active comparison of large replacement alternatives; it does not mean accepting unverified changes. Every selected transformation must still preserve the chosen compatibility contract and pass the applicable verification, review, rollback, and delivery gates.

This means the maintainer:

- searches every supported category and continues after the first fixes;
- originates and implements new repository-aligned features when codebase alignment, user value, acceptance criteria, compatibility, verification, and rollback evidence pass the proactive feature gate;
- considers module replacement, dependency removal, architecture migration, and whole-codebase rewrites;
- treats internal implementation as replaceable when public and observable behavior remains equivalent;
- captures baseline behavior and uses differential, golden, contract, property, compatibility, and performance checks as applicable;
- commits verified waves to a dedicated `autonomous-maintainer/<run-id>-<slug>` branch;
- prepares a fingerprinted PR candidate after verification, shows the complete inspection packet, and pauses until the user explicitly approves it;
- never force-pushes, pushes to the default branch, merges the PR, deploys, releases, exposes secrets, overwrites unrelated work, or weakens valid tests.

## Risks and safeguards

Aggressive transformations can expose incomplete contracts, change undocumented behavior, increase migration complexity, or produce a large review surface. The default workflow mitigates these risks by capturing observable behavior before replacement, strengthening contract tests, comparing baseline and candidate outputs, requiring independent review for high-risk work, retaining candidate-specific rollback evidence, and running three clean repository-wide rescans.

Branch-only delivery remains automatic, but pull-request delivery has a mandatory human gate. Before any candidate branch push or PR create/update operation, the skill shows the exact base/head refs and SHAs, commits, changed files and diff summary, verification results and blind spots, risks and rollback, delivery topology, proposed title/body, draft state, and a candidate fingerprint. It then returns `awaiting-user-pr-approval`. Only an explicit approval of that unchanged fingerprint allows the branch push and PR operation. The skill never pushes to the default branch or merges the PR. Use `pr_state=draft` for additional review after creation or `delivery=none` to keep all work local.

Proactive feature discovery does not mean arbitrary invention. A new feature needs at least three evidence points, including repository alignment and an independent user-value or demand signal, plus explicit acceptance criteria and a safe implementation and rollback path. Existing accepted behavior remains protected. Use `feature_policy=strong-evidence` to limit work to strongly evidenced missing behavior, `feature_policy=documented` to implement only promises already present in accepted sources, or `feature_policy=off` to disable user-visible feature additions.

## Required pre-PR inspection

For `delivery=pull-request`, the initial invocation is not approval. After all implementation, verification, review, and convergence work is complete, the run prepares an immutable candidate and pauses. Approve only after checking the displayed fingerprinted packet. Any later change to the base, head, diff, checks, topology, title, body, or draft state invalidates the approval and forces a new inspection. Rejecting the candidate leaves the PR unopened; requested revisions are applied and re-presented after verification.

## Install

Clone the repository:

```bash
git clone https://github.com/tkgo11/codex-autonomous-maintainer.git
cd codex-autonomous-maintainer
```

Standalone, user scope:

```bash
bash ./install.sh --variant standalone --scope user
```

OMX, user scope:

```bash
bash ./install.sh --variant omx --scope user
```

Project scope:

```bash
bash ./install.sh --variant standalone --scope project --project-dir /path/to/repository
bash ./install.sh --variant omx --scope project --project-dir /path/to/repository
```

PowerShell uses the equivalent `install.ps1` arguments.

## Invoke

Standalone:

```text
@autonomous-maintainer-standalone
```

OMX:

```text
$autonomous-maintainer
```

The explicit default invocation is:

```text
mode=apply focus=all feature_policy=proactive resume=true commit=checkpoint max_epochs=50 quiescence_scans=3 parallelism=auto network=public-read rewrite_policy=aggressive compatibility=observable-output delivery=pull-request pr_state=ready
```

Useful overrides:

```text
mode=report                         # read-only audit and transformation plan
rewrite_policy=surgical             # prefer localized changes
delivery=none                       # do not push or create a PR
pr_state=draft                      # create a draft PR
compatibility=public-contract       # preserve all documented public contracts
feature_policy=off                  # do not add user-visible features
feature_policy=documented           # add only features already promised by accepted sources
feature_policy=strong-evidence       # add strongly evidenced missing behavior, but do not originate features
```

## Observable-output compatibility

With the default `compatibility=observable-output`, private implementation, architecture, algorithms, dependencies, and file layout may change. The skill must preserve supported externally observable effects such as public API values and errors, CLI output and exit codes, serialization, emitted files, database effects, documented network behavior, UI-visible semantics, concurrency/cancellation/retry guarantees, and required performance ceilings.

The maintainer must record the comparison corpus and cannot treat missing, skipped, flaky, timed-out, or failed checks as proof of equivalence. Unsupported output differences reopen a finding instead of being normalized away.

## Development

```bash
make validate
make test
```

Direct validation:

```bash
python3 scripts/validate_skill.py SKILL.md
python3 scripts/validate_skill.py standalone/SKILL.md
```

Only the selected `SKILL.md` is required at runtime. The installers keep the two variants in separate skill directories so they may coexist.
