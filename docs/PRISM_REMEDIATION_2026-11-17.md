# PRISM remediation — 2026-11-17

Outcome: **PARTIAL_BLOCKED**. Two reproduced evidence-integrity defects are
fixed. `RC-STOPLOSS-ORDER-RECONCILIATION` remains unresolved. Keep PR #43 Draft.
No live or demo KIS authentication, balance inquiry, order, cancellation, or
modification was performed. Offline mocks do not establish live safety.

Run ID: `codex-prism-20261117-remediation`.
Initial checkout: `e17032bac72c0aebac8a1e4710b2c4b018abc986`.
Verified PR baseline: `8cb4d7a6540d7b8e339ca330fd3ed6d83b802ef5`.
Code fix: `6851f0131ba749903a8ab26f9ddf6ee0b3b8739b`.
PR base: `4a5c3c6c7e782ac9bf4cff135a93d85c08f823fb`.
Fork main observed: `d0a4d420bdd199a576a447788c704053b6550067`.
Upstream main observed: `fbf9762e90120247985e3fa95c432ba7ee7cbf93`.
Final delivery HEAD, CI, and lease release are recorded in the state-branch
run record; this document does not presume those future operations succeed.

## Primary risk gate

| Field | Evidence |
|---|---|
| prior_stalled_count | null; previous run did not establish a count |
| carry_forward_id | RC-STOPLOSS-ORDER-RECONCILIATION |
| latest_attempt_command_or_test_id | `python scripts/run_offline_pytest.py -q` |
| baseline_fail_observed | true for the two bounded subcases below; no claim of complete primary-risk reproduction |
| mock_result | baseline 2,794 passed; final 2,955 passed, exit 0 |
| code_fix_commit | 6851f0131ba749903a8ab26f9ddf6ee0b3b8739b |
| latest_head_CI | baseline run 43937874781 verified success; later delivery CI must be checked at its own HEAD |
| unverified_transitions | authoritative fill attribution; delayed/partial fill to actionable account-specific stop; cancellation/residual release; automatic restart recovery |
| exact_blocker | no implemented, validated persistent position-attribution/activation state machine; tracker remains keyed by market:ticker; independent execution audits and human reviews absent; quality findings unresolved |
| evidence_delta | 161 new mock cases; date provenance survives adapters/strategy/dispatch/ledger/restart; overlapping cumulative intervals rejected at inquiry/cache/restart |
| next_exact_action | design and mock-reproduce account+market+order+broker-date+execution-ID attribution through partial/cancel/restart into per-account stop quantities, with no automatic promotion from acceptance or aggregate holdings |

## End-to-end safety model

Signal validation and strategy selection precede serialized order submission.
KR/US adapters return acceptance or UNKNOWN, not confirmed fills. Strategies
map that result into StrategyExecution or BalanceSplitExecution. Dispatch
claims a token-fenced, account-scoped ledger identity before submission and
persists acceptance metadata after submission. Read-only history and discovery
collect bounded paginated evidence for operator triage. They never finalize
fills, clear an unresolved BUY, or authorize stop SELL. The watcher and dispatch
both fence automatic stops against unresolved BUY provenance. Restart loads
and validates persisted receipts and evidence, retaining unresolved claims.

The legacy status name `executed` still represents accepted submission on these
paths; this session did not redefine it as confirmed execution. Missing dates
remain unknown. Positive aggregate holdings cannot establish a new BUY fill.

## Reproduced changes

1. **RC-STOPLOSS-ORDER-RECONCILIATION / BROKER_DATE_LOSS**. Valid explicit broker
   dates were discarded by KR/US order adapters, dispatch and durable receipts.
   Independent review also found BalanceSplit's separate result type dropping
   the date. Preserve only matching, calendar-valid ASCII date aliases; never
   use the local clock or reservation dates. Retain legacy undated receipts.
   Reject persisted malformed dates and history/cache dates conflicting with
   the original receipt. Dates remain non-actionable provenance.
2. **RC-STOPLOSS-ORDER-RECONCILIATION / OVERLAPPING_CUMULATIVE_INTERVALS**.
   `(execution,cumulative)=(2,2),(3,6),(1,5)` summed to six but covered overlapping
   ranges with a missing range. The total-only check accepted the snapshot.
   Reject overlapping intervals of distinct execution IDs in the shared
   validator used by exact history, discovery, cached evidence and recovery.
   Preserve duplicate-ID idempotency and valid delayed read-only observations.

| Regression file | Actual baseline RED | Final cases |
|---|---|---|
| test_kr_broker_date_provenance.py | 18 failed, 9 passed | 27 passed |
| test_us_broker_date_provenance.py | 19 failed, 37 passed | 56 passed |
| test_broker_date_ledger.py | initial 8 failed, 5 passed; additional BalanceSplit 12 failed | 48 passed |
| test_execution_interval_gaps.py | 6 failed, 6 passed | 30 passed |

Tests were expanded after the first RED runs. RED counts therefore refer to
the cases present at that run, not every final test. An initial interval test
run contained two wrong expectations about an existing UNVERIFIED verdict;
those were corrected and six genuine product failures reconfirmed before the
production interval fix. Initial attempts using system Python failed because
pytest was missing; these were environment failures, not product RED evidence.

## Validation

Linux Python 3.12.14, pytest 9.2.0, isolated temporary virtual environment.
Current GitHub CI uses Python 3.13. Every recorded local pytest success ran
under a Linux seccomp filter denying non-Unix socket creation, inherited by
subprocesses. `unshare -n` was unavailable (EPERM); no weaker network fallback
was used. The reproducible filter is `scripts/run_offline_pytest.py` and requires
libseccomp. It self-checks IPv4/IPv6 denial before loading pytest.

| Command/scope | Observed result |
|---|---|
| full baseline, `/tmp/prism-verify-venv/bin/python /tmp/prism-offline/run_pytest.py -q` | 2,794 passed, 39.70s, exit 0 |
| full final, same offline runner | 2,955 passed, 43.61s, exit 0 |
| KR + adjacent | 140 passed |
| US + adjacent | 271 passed |
| ledger + adjacent | 238 passed |
| independent date/stop review | 282 passed |
| independent date cross-market review | 207 passed |
| interval + adjacent | 124 passed |
| independent adversarial interval review | 71 passed |
| pending BUY, account selection, interleaving, cross-process fences | 78 passed |
| Windows CI-selected six-file subset executed on Linux | 191 passed; this is not a Windows run |
| compileall, shell syntax, diff whitespace, pip check | exit 0 |
| original Docker build | exit 1; environment proxy CA certificate chain failure |
| temporary BuildKit CA-mount build | exit 0; no repository/image CA configuration changes |
| Docker network-disabled runtime import | exit 0; no trader construction/authentication |

The baseline CI run is [43937874781](https://github.com/tkgo11/prism-insight-light/actions/runs/43937874781),
at precisely `8cb4d7a`. The commit-workflow helper returned no runs because it
filters to PR events; the GET Actions API found the successful push run. No old
CI result is assigned to the new HEAD. Docker used an allowlisted clean context
whose runtime hashes match the code commit. Image SHA:
`76539bb641c5c8959d27b8737110f34f1184c02e12901c00fc77f540b444d5e2`.
No pip-audit or actual local Windows run was performed.

## Invariant coverage matrix

All results below are offline mocks. Existing baseline tests passed in the
2,794-test baseline; final existing and new tests passed in the 2,955-test run.
This matrix distinguishes fences and evidence inspection from completed
financial reconciliation. File names are within `tests/`.

| ID | Market / implementation | Existing test evidence | New coverage and remaining uncertainty |
|---|---|---|---|
| I01 | shared / outcome, dispatch | buy_order_receipt_durability: accepted receipt survives restart | acceptance remains non-actionable; actual fill attribution incomplete |
| I02 | shared / ledger | execution_outcome UNKNOWN tests | new adapter/ledger UNKNOWN dates retained; no promotion |
| I03 | KR+US / adapters, strategy, dispatch, ledger | strategy_buy_order_date_provenance_mock | fixed 131 new date cases across three files; absent broker dates remain unknown |
| I04 | KR+US / order_reconciliation | order_history_quantity_semantics: duplicate_paginated_execution_does_not_double_count | interval tests retain repeated IDs; no actionable fill state |
| I05 | shared / account scopes | readonly_buy_history_evidence_durability: same_receipt_across_two_accounts_stays_isolated | new distinct dates across two accounts; tracker attribution remains incomplete |
| I06 | KR+US / history correlation | unknown_buy_durable_history_correlation_readonly: history_from_other_scope_or_instrument_never_matches_durable_intent | existing scope fences pass |
| I07 | KR+US / interval validation | order_history_quantity_semantics: distinct_execution_sum_cannot_exceed_latest_cumulative_fill | fixed 30 interval cases; no position-book reconciliation |
| I08 | KR+US / read-only history | unknown_buy_receipt_history_partial_cancel_readonly: partial_cancel_residual_contradiction_discards_prior_page_and_keeps_fence | actual cancellation/residual finalization incomplete |
| I09 | KR+US / dedupe | order_history_quantity_semantics duplicate-page tests | interval duplicates/permutations pass |
| I10 | shared / durable writes | unknown_buy_intent_durability: intent_persistence_failure_blocks_broker | evidence write failure keeps UNKNOWN; not a transactional position ledger |
| I11 | shared / restart | readonly_buy_history_evidence_durability: readonly_evidence_persists_across_restart_without_fill_authority | new dated receipt restart tests; unresolved orders preserved |
| I12 | shared / recovery | same restart evidence test | corrupted interval/date cache rejected; recovery does not invent fills |
| I13 | shared / watcher, dispatch | pending_buy_stop_loss_safety: unreconciled_pending_buy_must_not_sell_preexisting_holdings | fail-closed confirmed; automatic activation still absent |
| I14 | shared / direct SELL fence | pending_buy_stop_loss_safety: direct_trigger_cannot_bypass_pending_buy_reconciliation | 78 related safety tests pass; no live safety claim |
| I15 | shared / pending provenance | pending_buy_stop_loss_safety preexisting-holdings tests | no inference from aggregate holdings; ownership accounting incomplete |
| I16 | shared / account selection | stop_loss_account_selection; same-receipt account evidence tests | selected-account inquiries covered; market:ticker tracker cannot model independent thresholds per account |
| I17 | KR+US / adapters/history | multi_account_domestic, multi_account_us | 27 KR and 56 US new date cases; market differences preserved |
| I18 | KR+US / pagination | kis_read_only_order_history: repeated_pagination_cursor_is_incomplete, pagination_limit_does_not_return_partial_proof | three-page interval corruption rejects earlier evidence |
| I19 | shared / claim token fencing | execution_ledger_retention: stale_callback_cannot_finalize_new_tokenized_attempt | new single/multi/queued replay remains suppressed |
| I20 | shared / persisted schema | readonly_buy_history_evidence_durability: corrupted_persisted_evidence_blocks_restart | invalid dates and checksum-valid conflicting dates/overlapping fills rejected |

Exact new test IDs and source/test blob SHAs are in
`prism-evidence-2026-11-17.json`. Existing equivalent cumulative-fill and
single-account tests were inspected before adding these distinct regressions.

## Agents, ownership and review

Six subagents ran concurrently, using all six available worker slots. KR owned
domestic adapter and its new tests; US owned US adapter and its tests; ledger
owned dispatch, ledger, BalanceSplit and ledger tests; stoploss performed
read-only safety investigation and independent integration review; tests owned
temporary environment/isolation/full validation; one worker combined upstream
compatibility and adversarial review, then owned interval validation/tests.
Lead owned shared date helper/common mapping, integration, commits and remote
writes. No subagent wrote remote state or committed. Initial investigations
were read-only; editing ownership did not overlap.

KR and US reviewed each other's adapters. Stoploss caught and rechecked the
BalanceSplit omission. KR independently attacked interval integrity including
checksum-valid corrupted recovery. The upstream/reviewer independently checked
date propagation across adapters and ledger. These are same-session reviews,
not separate scheduled executions.

## Additional candidates and upstream

| candidate_id / root_cause_id | Decision / evidence |
|---|---|
| DATE / BROKER_DATE_LOSS | FIXED; RED and GREEN above |
| INTERVAL / OVERLAPPING_CUMULATIVE_INTERVALS | FIXED; RED and GREEN above |
| PAGINATION / HISTORY_COMPLETENESS | ALREADY_COVERED; initial stale-checkout suspicion disproved on actual PR |
| WATCHER_RACE / BUY_SELL_SERIALIZATION | REJECTED; dispatcher rechecks guard inside serialized broker workflow; existing cross-process tests pass |
| ACTIVATION / RC-STOPLOSS-ORDER-RECONCILIATION | BLOCKED; full persistent attribution/activation implementation and evidence contract remain missing |
| UPSTREAM_AUTH / AUTH_RETRY | BLOCKED pending justified need and additional mock tests; no reproduced local auth defect |

No artificial ten-candidate quota. Upstream was examined in an isolated depth-50
bare clone, not an exhaustive history comparison. Latest BTC analysis changes
are outside fork runtime scope. Auth retry change `03902bbe9e1f6fe2fb6417b4e8129f276e293caa`
needs further testing. Exact SELL fractions and ADD rejection are already
covered in the fork. No upstream changes imported; no fork features deleted.

## Audit and delivery gates

Stage A related-path review is partial: targeted contracts/failure paths and
changed code reviewed, not every transitive file fully audited. Stage C changed
production/test scope reviewed by Lead and independent workers. Stages B and D
require separate authorized executions and were not performed. Related
independent count **0/2**, full independent count **0/2**. Historical manifest
reviews are not re-certified or incremented; changed blobs need fresh audits.

Fresh baseline remote status: Snyk success; Qlty success **with 89 blocking
issues**, untriaged and not PASS. Submitted human reviews: zero. Branch
protection read returned HTTP403; branch metadata's protected=false does not
substitute for complete policy verification. Primary financial gate unresolved.
No merge, approval, deployment, or live-trading enablement was attempted.

CAS lease acquisition used Connector-only Git objects: observed state parent
`86d63aa458411ac978f3d8ab18fe7afc32ff3745`, tree
`85076466a0884fae700b7b8c5d061b4eeb822c74`; created blob, tree, commit
`c949c86134db3a64b374be8729078b6064f65acf`, then updated ref with
`expected_sha` and `force=false`. Re-read confirmed this run ACTIVE. Release
must check current owner/head and use the same CAS protocol; final evidence is
in the state-branch run record.

Initial push was rejected by GH007 email privacy protection. The two unpublished
commits were recreated with a public automation noreply identity; code trees
were unchanged. No force-push or email-protection setting change was used.
