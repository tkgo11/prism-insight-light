# Repository audit — 2026-11-03

Status: verified local improvements; financial integration and external review
remain blocked. This is not a certification of trading safety or an exhaustive
proof that every defect has been eliminated. No live or demo broker orders,
production changes, merges, or deployments were performed.

## Scope and baselines

Repository: `tkgo11/prism-insight-light`. The initial clean checkout was
`53d4b8ecaefba0bd164c0b15fb872765fafff254` (1,210 tests passed). Work was moved
onto open PR #43 at `218e92fe98ee31c7a916c7f3ef7c65c12e308036` to preserve its
90 existing commits and avoid duplicate safety fixes (1,583 tests passed).
Current main `4a5c3c6c7e782ac9bf4cff135a93d85c08f823fb` was merged locally,
preserving its launcher and agent tooling additions. Initial user work was clean.

Read-only discovery covered subscriber lifecycle and Pub/Sub readiness, Telegram
ingestion, schemas and sizing, KR/US adapters and authentication, broker outcome
classification, strategies, durable ledger/queue/locks, stop-loss tracking,
WebUI routes/services/templates/browser behavior, configuration, dependencies,
Docker/cron/installers, CI, documentation, and related tests. Independent agents
reviewed the changed trading, ingestion, persistence, and security paths. This
was component-level review with focused adversarial testing; complete repeated
file-by-file financial audits, mutation testing, and broker integration were
not established. Those gaps prevent an unqualified convergence claim.

## Confirmed findings fixed in this run

| Severity | Root cause and correction | Evidence |
| --- | --- | --- |
| High | Caller cancellation released broker serialization while `to_thread` work continued. Shield and drain immediate and queued workflows through durable tracking before propagating cancellation. | Four regression cases cover repeated cancellation and worker success/failure; a competing workflow cannot enter early. |
| High | Stop-loss inquiries ignored named/indexed single-account selection and could inspect a zero-balance primary instead. Forward the dispatcher's selectors to the inquiry trader. | KR/US name/index and multi-account controls. |
| High | Newly added main `run.sh` discarded changes/commits using hard reset and continued after failures. Require a clean tree, fast-forward update, successful install, and use `exec` with forwarded arguments. | Four regression cases failed before correction and pass afterward; syntax and independent review pass. |
| Medium | Incomplete or structurally invalid queue objects wedged loading or remained incorrectly pending. Quarantine them as failed while valid records continue. | Six corrupt-record cases cover drain, reload, counts, and subsequent enqueue. |
| Medium | Telegram used the newest message as the older-page cursor, returned pages out of chronological order, and truncated away newer posts. Use the oldest numeric cursor, detect nonprogress, select newest limited posts, and order the whole batch chronologically. | Multi-page IDs, numeric ordering, newest limit, and BUY-before-SELL tests. |
| Medium | Telegram allocated sessions without closing them. Close internally owned clients on success/error and preserve caller-owned clients. | Ownership, error cleanup, zero-limit, and invalid-channel cases. No throughput claim. |
| Medium | Telegram persisted channel-local IDs without channel identity and failed open on unreadable suppression state. Store normalized channel maps; lock, merge, atomically replace/fsync; verify persistence before every poll; pause and recover conservatively. | Fourteen new persistence cases include restart, aliases, legacy migration, stale/concurrent writers, corruption, unknown formats, unwritable state, recovery, retention, and failed replacement cleanup. |
| Medium security | Log masking operated line by line and exposed multiline PEM bodies. Mask complete/open/orphaned/truncated key fragments before display. | Three bounded-tail regressions; independently reviewed. |
| Medium | Non-ASCII CSRF form input and valid UTF-16 JSON caused server errors. Compare token bytes and use the existing strict raw-byte JSON loader. | Invalid Unicode token, configured Unicode token, UTF-16 validation and duplicate-key controls. |
| Medium | Dry-run form/API decoded ambiguous JSON and rounded fractional integer literals before validation. Reuse strict JSON parsing, including outer API keys. | Five simulator regressions reject duplicates and exact fractional integer literals. |
| Medium dependency | Installed transitive `pyasn1` 0.6.4 had four published advisories. Require `pyasn1>=0.6.5,<0.7.0`. | Audit before: CVE-2026-119194/119195/119196/119197. After installation: compatible dependencies; no known vulnerabilities reported. Application exploitability was not established. |
| Low | Browser requested a missing favicon and failed the existing browser error gate. Serve the existing PRISM brand mark as an SVG favicon. | Full browser smoke failed before and passed after. |
| Medium CI, PR #39 | Packaged workflow changed without updating its checksum. Refresh only that manifest row on a separate PR-specific worktree. | Checksum failed before; all 19 checksums, validators and Bash installer smoke pass afterward. |

No framework or library replacement was justified. The only dependency change
is a security floor for an already-installed transitive dependency. No measured
performance improvement is claimed; test timing is verification evidence only.

## PR audit

All five open PRs were read through GitHub, including changed files, heads,
reviews and check results. No submitted reviews were present. All inspected
heads had successful Linux and Windows Actions checks.

| PR | Head inspected | Result |
| --- | --- | --- |
| [#43](https://github.com/tkgo11/prism-insight-light/pull/43) | `218e92f` | Reviewed 90-commit history and safety changes; use as primary integration base. Remains Draft. Its green Qlty status says **94 blocking issues**, not zero issues. Snyk says no manifest changes; the separate local dependency audit was necessary. |
| [#40](https://github.com/tkgo11/prism-insight-light/pull/40) | `88267e0` | setup-python v7 SHA upgrade; no confirmed local defect in its patch. |
| [#39](https://github.com/tkgo11/prism-insight-light/pull/39) | `6844062` | checkout v7 upgrade includes packaged workflow; checksum correction prepared at `eae73d8`. |
| [#37](https://github.com/tkgo11/prism-insight-light/pull/37) | `1e56306` | Python 3.15 CI/Docker upgrade; remote checks green. Local verification here used Python 3.13, so no local Python 3.15 compatibility claim. |
| [#36](https://github.com/tkgo11/prism-insight-light/pull/36) | `dc437a0` | checkout v4.4 SHA upgrade; overlaps the exact root workflow lines in #39. Select one upgrade before merging both alternatives. |

The open non-PR issue is dependency dashboard #38. Main was not overwritten by
the older PR base. No PR was merged and no required review was bypassed.

## Verification

- Final full suite: **1,637 passed**, five existing pandas market-calendar
  deprecation warnings, 20.03 seconds; Python 3.13.5. This adds 54 test cases to
  the PR #43 baseline. Defects were reproduced before their fixes.
- Affected suites and independent review: passed. These use mocked broker,
  Telegram, and Pub/Sub boundaries rather than real orders or credentials.
- `compileall`, `bash -n` for all four root scripts, and Git diff hygiene: passed.
- Chromium/Playwright fixture smoke: passed all nine routes, responsive layout,
  themes, command palette, table filters/pagination/date sort, CSV safety,
  simulator handoff, and no-JavaScript fallback, with no browser errors.
- `uv pip check`: passed. `pip-audit` against the installed environment: changed
  from four advisories in one package to no known vulnerabilities. This does
  not prove absence of undisclosed vulnerabilities or audit the OS image.
- Docker runtime build: passed, followed by network-disabled schema/WebUI
  import smoke. A temporary Dockerfile adds only a BuildKit CA secret mount
  for this environment's HTTPS proxy; the repository Dockerfile was unchanged.
  Final image: `sha256:3efceae236bb9bb3fcc3e6053fcd9cd0a69e049b69d28275f9ee68cd5a4b2555`.
- Imported main agent package: both structural validators and all 19 checksums
  passed. PR #39 correction additionally passed its Bash installer suite.
- Not run locally: Windows, Python 3.15, PowerShell installers, broker fill or
  cancellation integration, production deployment, mutation/fuzz campaigns,
  OS vulnerability scan, and before/after performance benchmarks.
- Reproducing tests and the initial browser favicon failure were expected red
  evidence and were fixed. No remaining local test failure is hidden.

## Commits and files

Primary branch: `autonomous-maintainer/20261103-runtime-audit`.
Verified waves: `7b6c56a` trading cancellation/account selection; `c38df08` queue
recovery; `c937eba` Telegram fetching; `139031c` WebUI; `cd7e8ab` dependency
floor; `8c98a74` main integration; `bd5fde7` Telegram persistence/docs;
`09d26cb` launcher/CI; `8adabc1` favicon. This report is a separate docs commit.

Changed runtime files: `subscriber.py`, `trading/dispatch.py`,
`trading/off_hours_queue.py`, `trading/stop_loss_watcher.py`,
`trading/telegram_fetch.py`, WebUI guards/signals/dry-run/log service, base
template/favicon, `requirements.txt`, `run.sh`, root tests workflow, English
and Korean READMEs. Regression files cover dispatch cancellation, watcher
account selection, corrupt queue recovery, launcher failures, Telegram fetch
and worker state, WebUI routes, logs and dry-run parsing. Original PR #43's
files and commits are preserved rather than counted as this run's fixes.

Separate branch: `autonomous-maintainer/20261103-pr39-checksum`, worktree
`/workspace/prism-checkout-pr39`, commit
`eae73d8378cf6b77d2a0dd2b743dbf4c17570b53`; only the packaged checksum file
changed relative to #39. No branch has been pushed by this run.

## Remaining blockers and compatibility risks

1. Broker-authoritative per-account order/fill/partial-fill/cancellation and
   remaining-quantity reconciliation is still unavailable. Pending/unknown
   BUYs stay non-actionable; automatic protection of a later-filled BUY is
   **not proven**. No broker credentials or authoritative integration fixtures
   were available. Retain Draft and existing live-deployment restrictions.
2. Stop-loss state is still instrument-level across accounts. Independent full
   financial audits and human review remain incomplete. Forced process death
   cannot drain threads; durable claims, not cancellation shielding alone,
   protect restart/replay. Simultaneous subscribers rely on the execution ledger.
3. The external Qlty status reports 94 blocking findings without accessible
   individual evidence in the inspected GitHub result; these were not claimed
   resolved. Branch-protection lookup returned integration HTTP 403, so policy
   could not be independently verified. No merge was attempted.
4. Legacy Telegram ID lists lack origin metadata. Migration binds them to the
   first configured channel; operators must preserve that channel or reconcile
   history. Older releases cannot read the new map: rollback requires an
   explicit channel-specific export and reconciliation, never deletion of
   the execution ledger or blind removal of suppression state.
5. Cancellation now waits for bounded broker work to finish. This intentionally
   delays task cancellation; forced shutdown remains outside this guarantee.
6. A byte-truncated log tail masks ambiguous bare base64-only lines, which may
   hide rare innocuous diagnostic text. Normal timestamped records remain.

Rollback other waves by reverting their scoped commits after review. Never
delete trading ledgers to roll back code. Broader architecture replacement,
new trading features, or runtime migration lacked sufficient compatibility
evidence to justify increasing this financial system's risk.
