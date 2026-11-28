# PRISM continuation: ownership evidence blocks final audit campaign

Outcome: **PARTIAL_BLOCKED**. This session implements a bounded, non-actionable
evidence tranche. It does not complete account-owned quantity reconciliation,
automatic stop activation, or the requested final Subagent 2+2 campaign.

Run: `codex-prism-20261117-subagent-audits`.
Verified baseline: `f3527787a95d22d4915ae954e7a1528f39b6ebde`.
Code candidate: `10e743f9bb323a44ebe9065b8df4c58c978de12c`.
The final delivery SHA and exact-HEAD CI are recorded in the state-branch run
record after delivery. No old CI result is assigned to the new candidate.

## Methodology change and counters

The user explicitly replaced four separate Cloud executions with four
independent Subagent assignments for the new audit campaign. That changes the
methodology; it does not satisfy the original execution-based requirements.

| Counter | Actual result |
|---|---|
| subagent_related_audits | 0/2 |
| subagent_full_audits | 0/2 |
| original_related_execution_audits | 0/2, unchanged |
| original_full_execution_audits | 0/2, unchanged |

AUDIT-R1, AUDIT-R2, AUDIT-F1 and AUDIT-F2 have **not started**. The user requires
critical remediation to finish before final audits. The ownership evidence
blocker below prevents that prerequisite. Six engineering workers investigated,
implemented and independently reviewed this tranche; none is counted as one
of the four final audits. No prewritten PASS or sampled full-scope audit is
being substituted. A frozen delivery inventory is not an audit PASS.

## Newly confirmed technical blocker

The user confirmed that external/manual transactions in these accounts are
possible. Original BUY-order history plus aggregate holdings cannot determine
current inventory attributable to that strategy order:

| World | Events | Original BUY history | Current balance | Original strategy inventory |
|---|---|---|---|---|
| A | strategy BUY 4 | same four fills | 4 | 4 |
| B | strategy BUY 4, external SELL 4, external BUY 4 | same four fills | 4 | 0 |

Both observable inputs are identical. An algorithm using only those inputs
cannot distinguish A from B. `min(balance, historical_filled)` and a fraction
of aggregate holdings do not solve this. Allowing SELL would risk liquidating
unverified replacement/preexisting inventory. Two KR/US regression cases model
this counterexample and require ownership to remain unknown.

Missing inputs/contracts are complete account transaction and transfer evidence
since acquisition, including externally submitted SELLs, ordering and pagination
coverage, corrections/corporate actions, and a defined inventory-allocation
policy. Current normalized order-history records also do not prove canceled
quantity/root-child cancellation completion. Existing SELL ledger entries only
record side, not the scope and allocated quantity needed to consume lots.

No exclusive-account assumption was introduced. No historical fill summary is
promoted to current ownership. Every new summary explicitly returns
`current_owned_quantity=None` and `can_activate_stop=False`; UNKNOWN, dedupe and
watcher fences remain in place. This avoids an unintended SELL but does not
prove that delayed/partially filled BUYs receive automatic stop protection.

## Actual implementation

1. Preserve `order_kind`, submitted `order_quantity` and US `order_venue` through
   KR/US adapters, StrategyExecution/BalanceSplitExecution, DispatchResult,
   account legs and durable BUY receipts. Reservation endpoints always produce
   the `reservation` namespace, even when a response contains ODNO. Actual
   ordinary order IDs use `broker_order`. Missing legacy metadata is not
   invented. Quantity is the actual sync submission quantity, never a fill.
2. `summarize_buy_fill_evidence()` checks original normal-order namespace,
   submitted-quantity bound, exact account/market/ticker/order/date/venue,
   execution-ID dedupe and contiguous cumulative intervals. It summarizes
   `filled_terminal` or `partial_open`, without ownership or trading authority.
   Cancellation flags without confirmed cancellation quantities remain
   unverified. All target US history rows must explicitly agree on venue.
3. `ExecutionLedger.record_reconciled_buy_evidence()` atomically stores canonical
   history, fingerprint, recomputable summary and timezone-aware capture time.
   Restart recomputes it against the original intent/receipt. Conflicting or
   regressing executions, ambiguous duplicate receipts, malformed state and
   write errors fail closed. Identical notifications do not rewrite state.
   `get_reconciled_buy_evidence()` exposes historical evidence, not positions.
4. Linux CI now invokes the persisted mandatory seccomp pytest runner. Missing
   libseccomp/filter installation fails before pytest; there is no network
   fallback. Windows smoke is unchanged and is not claimed network-isolated.

These APIs do not initiate automatic broker inquiries or orders. Caller-provided
normalized history remains non-actionable. A structurally valid dictionary is
not cryptographic proof of a broker response. No new runtime scheduler or safe
position-book/exact-quantity SELL/exit reservation pipeline is claimed.

## RED evidence and validation

System Python initially lacked pytest in the preceding session; the temporary
verification venv and checked-in offline runner were revalidated here.
Some temporary collection failures occurred while the ledger imported a helper
another worker had not finished; they are integration timing failures, not
product RED. Dispatch baseline was then isolated by loading the unchanged
baseline ledger from Git into memory without reverting shared files.

| Boundary | Observed RED / classification |
|---|---|
| KR adapter metadata | 15 assertion failures before fix; tests expanded to 29 |
| US adapter metadata | 29 assertion failures before fix; tests expanded to 41 |
| strategy/receipt mapping | 12 assertion failures, 6 controls passed |
| ledger rich receipt | 2 assertion failures before schema fix |
| new summary API | initial 62 missing-capability failures; explicitly not existing unsafe trading defects |
| stop/external-inventory tests | new feature safety controls; no invented baseline defect RED |

| Validation | Actual result |
|---|---|
| baseline f352778, persisted offline runner | 2,955 passed, 45.00s, exit 0 |
| final working code, same runner | 3,185 passed, 53.49s, exit 0 |
| new files | 194 cases across seven files |
| expanded existing date integration | 36 additional cases; single/multi-account, queued, KR/US, legacy/modern, accepted/UNKNOWN |
| Lead mapping/date integration | 102 passed |
| independent bounded-tranche review | 194 passed |
| watcher/guard integration and adjacent | 107 passed |
| Windows CI-selected subset run on Linux | 191 passed, 7.00s; not a Windows run |
| compileall, shell syntax, diff check, pip check | exit 0 |
| seccomp socket/subprocess selftest | 6 passed; IPv4/IPv6 TCP/UDP denied, Unix IPC works |
| clean-context CA-assisted Docker build | exit 0; prior proxy-CA workaround, no repository/image CA configuration change |
| Docker network-none runtime imports | exit 0; synthetic config, no trader construction/authentication |

Local Python is 3.12.14; hosted CI is Python 3.13. The local Docker image is
`sha256:f61d8377128c1de29b6a55dc2e33a950f4cd1604bc6247a3b9cfb1abfd9d989d`.
Runtime hashes match the clean allowlisted build context. No live/demo brokerage
authentication, balance query, order, cancellation or modification occurred.

## Engineering ownership and independent review

Six existing engineering workers were reactivated, separate from final audits:
KR owned domestic metadata and pure summary/history validation; US owned US
metadata; ledger owned durable schema/recovery; stoploss owned integrated
no-activation tests; tests owned regression/build/CI; adversarial review owned
the external-inventory counterexample and independent implementation review.
Lead owned shared metadata validation, strategy/dispatch mapping and integration.
Only Lead performed commits or shared remote writes.

KR and US reviewed one another's adapters. The adversarial worker independently
reviewed the summary, ledger, helper, dispatch, adapters, strategies, CI and
integration tests. Namespace confusion, quantity invention, duplicate receipts,
monotonicity, persisted corruption, false ownership and premature stop activation
were specifically checked. That worker's conclusions are engineering review
evidence only. Final audit contexts were not created or credited.

New/extended test IDs, exact blobs, validation log hashes, defined related/full
file inventories and review scope are in the companion JSON evidence and final
state-branch manifest. Inventory functions are navigation metadata, not claims
that every function was audited.

## External gates and next action

Revalidated baseline f352778 CI run 44081153868 succeeded. Snyk succeeded; Qlty
reported success with **89 blocking issues**, not individually triaged and not
an overall PASS. Human reviews were empty. Branch-protection lookup returned
HTTP403; one transient PR-read protocol error was retried successfully.
Final-head statuses must be recorded separately after pushing this tranche.

Keep PR #43 Draft. Do not merge or deploy this as completed stop protection.
No upstream feature batch was imported. Existing fork functionality remains.

Exact next action: define and provide an offline, source-backed complete-account
transaction/transfer event contract, then reproduce the indistinguishable worlds
with those new inputs; implement a versioned allocation book for all BUY/SELL
paths, exact account/quantity-bound durable exit reservations and confirmed SELL
consumption. Obtain explicit cancellation quantity/root-chain evidence before
terminal partial-cancel release. Only after this critical path is verified may
a new immutable candidate begin the four independent final Subagent audits.

CAS acquisition: state parent `97e9a117aeab2bebc2c3531a24ede594525d2c0a`, acquired
commit `d086ad4c1a0f9eb99c387c1e8cc490aef302d14f`, `expected_sha` checked and
`force=false`, owner re-read ACTIVE. Final release evidence is recorded by the
Lead in the state branch after exact-HEAD CI verification.
