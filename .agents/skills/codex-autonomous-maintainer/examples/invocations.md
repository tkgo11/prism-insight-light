# Invocation examples

## Default aggressive transformation and user-approved PR

Standalone:

```text
@autonomous-maintainer-standalone
```

OMX:

```text
$autonomous-maintainer
```

Equivalent explicit options:

```text
mode=apply focus=all feature_policy=proactive resume=true commit=checkpoint max_epochs=50 quiescence_scans=3 parallelism=auto network=public-read rewrite_policy=aggressive compatibility=observable-output delivery=pull-request pr_state=ready
```

The default proactively originates and implements repository-aligned features that pass its evidence, acceptance, compatibility, verification, and rollback gates. It may also replace modules, dependencies, architecture, or the entire implementation when differential verification proves accepted observable behavior is preserved. After final verification it presents a fingerprinted PR inspection packet and returns `awaiting-user-pr-approval`. Only explicit approval of that unchanged candidate permits the dedicated-branch push and ready-for-review PR creation or update. It never merges the PR.

Use this default only when broad autonomous changes and user-approved PR delivery are intended. Start with `mode=report`, `rewrite_policy=surgical`, `pr_state=draft`, or `delivery=none` when the repository contract or desired scope is uncertain.

## Approve the prepared candidate

When the run returns `awaiting-user-pr-approval`, inspect the displayed base/head, diff summary, checks, risks, title, body, draft state, and fingerprint. Reply with an explicit approval that identifies the current candidate. A previous approval or a generic acknowledgement does not authorize a changed candidate.

## Draft PR

```text
@autonomous-maintainer-standalone pr_state=draft
```

## Keep work local

```text
@autonomous-maintainer-standalone delivery=none
```

## Push a branch without opening a PR

```text
@autonomous-maintainer-standalone delivery=branch
```

## Prefer surgical changes

```text
@autonomous-maintainer-standalone rewrite_policy=surgical
```

## Preserve documented public contracts, not only outputs

```text
@autonomous-maintainer-standalone compatibility=public-contract
```

## Read-only aggressive audit

```text
@autonomous-maintainer-standalone mode=report
```

## Disable autonomous features

```text
@autonomous-maintainer-standalone feature_policy=off
```

## Limit features to existing evidence

```text
@autonomous-maintainer-standalone feature_policy=strong-evidence
```

Use `feature_policy=documented` for only explicitly promised behavior. The default `proactive` policy may originate new features, but only with repository-alignment evidence, an independent user-value or demand signal, and testable acceptance criteria.

## Constrained run

```text
@autonomous-maintainer-standalone "do not modify frontend/ or add runtime dependencies"
```

## Require a draft PR for architecture replacement

```text
@autonomous-maintainer-standalone rewrite_policy=aggressive pr_state=draft "preserve the documented CLI and persisted file formats"
```

## Handle missing verification prerequisites

```text
@autonomous-maintainer-standalone "do not install undeclared tools; record missing test runtimes as blocked-environment"
```

A missing compiler, test fixture, credential, or service does not become a pass. The run records the exact blocked evidence and delivers only independently verified work when safe.

## Prevent delivery when origin or permissions are wrong

```text
@autonomous-maintainer-standalone delivery=pull-request "do not create a fork or use a different remote when origin validation or write permission fails"
```

The run stops the delivery phase rather than pushing elsewhere, force-pushing, or targeting the default branch directly.

## Resume

```text
@autonomous-maintainer-standalone resume
```

## Supported options

| Option | Values | Default |
|---|---|---|
| `mode` | `apply`, `report` | `apply` |
| `focus` | `all` or categories | `all` |
| `feature_policy` | `off`, `documented`, `strong-evidence`, `proactive` | `proactive` |
| `resume` | `true`, `false` | `true` |
| `commit` | `false`, `checkpoint`, `final` | `checkpoint` |
| `max_epochs` | `1..100` | `50` |
| `quiescence_scans` | `1..10` | `3` |
| `parallelism` | `auto`, `1..32` | `auto` |
| `network` | `off`, `public-read` | `public-read` |
| `rewrite_policy` | `surgical`, `allow`, `aggressive` | `aggressive` |
| `compatibility` | `observable-output`, `public-contract`, `strict-internals` | `observable-output` |
| `delivery` | `none`, `branch`, `pull-request` | `pull-request` |
| `pr_state` | `draft`, `ready` | `ready` |
