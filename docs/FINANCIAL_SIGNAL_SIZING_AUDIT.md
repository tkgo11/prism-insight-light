# Financial signal sizing compatibility audit — PR #42

> Scope: `dragon1086/prism-insight` publisher BUY/SELL sizing → `tkgo11/prism-insight-light` subscriber, branch `feat/upstream-signal-sizing-20261031`. **Static/CI audit, NOT live brokerage certification. Do not enable real orders solely based on CI.**

## Contract and test invariants

1. Parse inbound `BUY.position_fraction` in (0,1], default 1.0; `SELL.sell_denominator` as a positive integer, default 1. Unsupported `ADD` **must not** become BUY.
2. For each account, determine one **local configured order budget** before applying upstream BUY fraction. Fixed-amount settings retain their old semantics. Percent-of-assets settings use the account's `total_eval_amount` and cap by verified `available_amount`; a missing summary or zero orderable cash must reject the fractional order without broker submission.
3. A strategy's explicit BUY notional is the base; multiply upstream fraction **exactly once**. A fractional default BUY must not silently fall back to full size.
4. For SELL, apply the strategy-specific exit fraction divided by the upstream denominator; the broker uses `floor(verified_holding * fraction)`, never round up. Zero orderable shares produce no SELL order.
5. Execute the **same** order-sizing semantics in legacy, strategy, KR, US, and multi-account paths; each account has an independent budget, holdings, and durable execution identity.
6. Replay and queued payloads preserve the original sizing fields. Unknown broker outcomes must never be automatically retried. Pre-submission sizing rejections must not be mistaken for broker-accepted orders.
7. Partial exits **must not** delete trailing-stop records or stop-loss protection while holdings remain. For a configured exit strategy, the watcher retains tracking unless a fresh post-order holding check proves flat.
8. `event_risk_off.buy_size_multiplier` must work even if the source payload does not supply `buy_amount`; combine it with upstream fraction once.

## Related source paths reviewed against this contract

| Group | Source files | Invariant |
| --- | --- | --- |
| Inbound parsing | `trading/schema.py`, `subscriber.py`, `trading/telegram_fetch.py`, `trading/signal_safety.py` | 1, 6 |
| Execution orchestration | `trading/dispatch.py`, `trading/execution_ledger.py`, `trading/execution_outcome.py`, `trading/off_hours_queue.py`, `trading/file_lock.py` | 3–7 |
| Broker budget + orders | `trading/signal_sizing.py`, `trading/buy_sizing.py`, `trading/domestic.py`, `trading/us.py`, `trading/kis_auth.py` | 2–5 |
| BUY and combined strategies | `trading/strategies/common.py`, `balance_split.py`, `score_max_capital.py`, `score_risk.py`, `score_weighted.py`, `risk_bracket.py`, `balanced_risk.py` | 2–5 |
| SELL and mixed strategies | `trading/strategies/bracket_exit.py`, `protective_exit.py`, `profit_ladder.py`, `stop_loss_sell.py`, `signal_trailing_stop.py`, `event_risk_off.py`, `cooldown.py`, `limit_buffer.py` | 3–4, 7–8 |
| Protection and external controls | `trading/stop_loss_watcher.py`, `webui/services/trade_service.py`, `account_service.py`, `signal_service.py`, `webui/routes/trading.py`, `trading/config/kis_devlp.yaml.example` | 5–7 |
| Operational entry points | `setup_subscriber_crontab.sh`, `setup_subscriber_docker_crontab.sh`, `pubsub_readiness.py`, `webui/services/dry_run_service.py` | Verify they delegate to shared execution/validation rather than independently sizing |

## Evidence in repository tests

- `tests/test_upstream_signal_sizing.py`: schema validation, legacy defaults, unsupported ADD, KR/US basic sizing.
- `tests/test_upstream_strategy_compatibility.py`: trailing stop, score-max-capital composition, zero-cash failure, rejection outcome.
- `tests/test_financial_sizing_audit.py`: KR/US percent-of-assets budgets, missing cash fail-closed, risk-off multiplication.
- `tests/test_partial_exit_stop_loss_safety.py`: watcher keeps residual holdings protected, retains record on failed balance inquiry.
- `tests/test_strategy_partial_matrix.py`: 10 BUY strategy configurations and 9 SELL strategy configurations.
- `tests/test_queue_and_account_sizing.py`: serialized queue retains sizing and each KIS account uses its own budget.
- `tests/test_effective_order_metadata.py`: strategy messages and detailed order metadata reflect the **effective** fraction/budget sent to the broker, preserving unscaled strategy bases separately.
- `tests/test_stop_loss_legacy_quarantine.py`: duplicate-list fail-closed quarantine, preservation of all legacy records, and already-migrated conflicting entries.
- Existing `tests/test_multi_account_*.py`, `tests/test_stop_loss_watcher.py`, `tests/test_dispatch.py`, `tests/test_signal_safety.py` and `tests/test_wave4_execution_safety.py` remain regression guards.

## Critical limitations and residual risks (not waived)

- **Ambiguous JSON ledger recovery (2026-11-01):** Python's default JSON parser silently accepts duplicate object keys and retains the last value. A recovered or manually edited execution ledger could therefore replace a non-retryable `executed` status with `failed`, and a reservation ledger could replace an outstanding amount with zero. Stop-loss positions, automatic execution claims, off-hours queued orders, and balance-split reservations now reject duplicate keys at every JSON object depth instead of accepting an arbitrary last value. Read failures prevent automatic execution and preserve the original file for backed-up operator reconciliation; the new tests must still pass on the final PR head. This does **not** resolve missing/deleted files, broker order-status reconciliation, or crash durability and is not approval for live trading.

- **Balance-split reservation safety:** a lower cash balance does not prove any particular accepted BUY filled; unrelated withdrawals/transfers/FX can cause the same decrease. Reservations now remain until authoritative per-order reconciliation and operator clearance. This may conservatively block additional BUYs; automatic retirement is intentionally disabled pending a safe broker order-status ledger.
- **Order acceptance ≠ fill.** Broker `success=True` may not establish complete exchange execution. Position reconciliation, partial fills, pending orders, cancellation and amendments need a read-only KIS demo integration harness and account-level post-trade verification before live use.
- **No complete live KIS or live GCP Pub/Sub run here.** Both domestic/overseas, regular/off-hours, real/demo, multi-account and auto-exchange need controlled integration tests with actual sandbox credentials and non-financial limits.
- **Stop-loss partial liquidation:** tracking is retained for configured strategy exits, but ledger idempotency may suppress repeated watcher-generated signals after a previously accepted order. Remaining holdings require operator reconciliation; automatically issuing another order without confirming the first is filled would be unsafe.
- **Conflicting legacy stop-loss entries:** when an old list contains multiple entries for the same market/ticker, **all** copies are quarantined under `__legacy_unverified_<index>__`; the watcher does not choose a stop price or auto-sell that instrument. Already-migrated dictionaries with a matching quarantined instrument are likewise blocked. US ticker case variants (for example, lowercase vs uppercase AAPL) are treated as one logical instrument; conflicting legacy entries are quarantined and duplicate active dictionary records block automatic selling. Original records remain on disk for human reconciliation. An operator must inspect broker holdings and outstanding orders, resolve conflicting stop levels, and remove the quarantined evidence through a deliberate backed-up recovery procedure before automatic protection can resume. Re-registering a position alone does not override unresolved quarantine. Independent instruments continue to be monitored. An error log is emitted for blocked instruments; this is a **manual intervention required** state, not evidence that the position is protected.
- **New US stop-loss registrations (2026-11-02):** case-variant ticker inputs now use one uppercase durable identity. Existing case-variant or quarantined records are never silently overwritten; attempts to rearm an unresolved instrument raise an explicit operator-reconciliation error and preserve the original bytes. Existing legacy lower-case records remain readable but require operator review before a conflicting new registration. The stop-loss ledger now writes to unique same-directory temporary files and atomically replaces the destination; this avoids collisions when the configured filename itself ends in `.tmp`. Neither change resolves broker fill reconciliation or guarantees crash durability on every filesystem.
- **Small fractional quantities:** an order can round to zero whole shares; the broker rejects/does not submit. This is not a full-position exit, and no share count should be inferred from a requested fraction alone.
- **Partial publisher exits combined with strategy exits:** the effective fraction is `strategy_sell_fraction / sell_denominator`; this is deliberately conservative and may differ from the upstream standalone subscriber's exact liquidation quantity.
- **`ADD` is intentionally unsupported** pending idempotency, hold checks, and per-position caps.
- **Do not claim formal verification or a perfect financial system:** simulations, mocks, code review and CI are necessary but insufficient for production guarantees.

## Deployment gate

Keep the PR unmerged until a human reviews all modified logic and a separate demo-environment integration test verifies no unexpected trade, no account mix-up, idempotency across restarts, price-limit checks, filled vs accepted status handling, residual holdings after partial sells and all stop-loss watcher transitions. Use a manual kill switch and small demo positions in all testing.


### Strategy runtime-state integrity (2026-11-02)
- Cooldown, signal-trailing-stop, risk-bracket metadata, and event-risk-off
  share strategy-state JSON helpers. A malformed, duplicate-key, nonfinite,
  structurally invalid, or unreadable existing file must **fail closed**:
  it cannot be treated as an empty state or overwritten by a later write.
- A failed atomic state write must propagate to the dispatcher. If an order
  might already have been accepted, the execution ledger must retain an
  unresolved identity for manual broker reconciliation; a green unit test is
  not proof of an exchange fill.
- Operators must preserve the original file, inspect the broker's order/fill
  history and position holdings, and restore verified state before restarting
  affected automated strategies. Do not simply delete the state to resume.
- Tests cover corrupt JSON, duplicate keys, nonfinite data, bad timestamps,
  failed atomic replace, and mock-broker no-submit on corrupted protection.
  KIS demo order-ID/fill/cancel reconciliation remains unverified.

- Semantic trailing-stop records are also checked for a unique instrument key,
  matching market/ticker, and a finite positive high-water mark. A string
  `"nan"`/`"inf"` in otherwise valid JSON must not bypass the stop trigger.

### Pending-order fences for signal-trailing-stop (2026-11-02)

- The cooldown strategy also retains **accepted** broker orders as
  `pending=True` beyond the nominal cooldown window, not only timeouts and
  unknown results. Its broker order ID is retained when available. The normal
  time window applies only after an independently verified final fill/cancel
  reconciliation clears pending; no automatic expiry proves a fill.
- A BUY broker submission is **not** evidence of a filled position. Before
  submission the strategy writes an instrument-scoped `entry_pending` record.
  Accepted, ambiguous and transport-error results keep the fence across process
  restarts; a verified pre-acceptance rejection restores the exact previous
  protection record (or removes a newly created pending record).
- A SELL with `require_tracked_entry=False` also writes a durable
  `exit_pending` record **before** broker submission. Otherwise untracked
  pass-through could submit multiple exits with distinct signal IDs. Both
  pending directions block later BUY and SELL for the same instrument until
  verified broker order/fill/remaining-quantity reconciliation.
- An order ID stored as `entry_broker_order_id` or `exit_broker_order_id`
  proves at most submission/acceptance, **not** exchange execution. Do not
  automatically remove pending markers after `success=True`, after a timer,
  or because an unrelated cash/holdings snapshot changed.
- This is deliberately conservative and may suspend a strategy until an
  operator reconciles the KIS demo order history and account-specific fills.
  A test that clears pending after a *simulated verified fill* is a mock, not
  evidence of real KIS integration. Manual reconciliation must preserve
  backups and outstanding-order evidence; no live trading is authorized.
- Remaining limits: account-scoped identities, automatic order-ID/fill/cancel
  reconciliation, durable recovery across every filesystem, and end-to-end
  KIS demo validation are not yet established.


### Queued execution authorization scope (2026-11-02)

Queue replay now pins the original demo/real trading mode and the original
single-account selector (stored as a SHA-256 digest, not an account label), or
the explicit multi-account target set. The queue identity includes these
execution-scope fields so distinct authorized contexts do not collapse into one
entry. Missing legacy context, changed trading mode, and changed single-account
selector are quarantined for operator reconciliation **before any broker call**.
This does not prove that an unchanged primary selector still resolves to the
same underlying account after a configuration edit: account-configuration
identity reconciliation remains a separate deployment gate. Broker order
acceptance still does not prove fill; KIS demo order-ID, partial-fill, cancel
and remaining-quantity checks are still required. No live trading was tested.

The actual KIS account key is also resolved at enqueue time and stored only as
an opaque hash. Replay resolves it again and quarantines an item when the
primary account or named/indexed account now points at a different KIS key.
Missing account configuration prevents enqueue; legacy entries without this
pin require manual reconciliation. This does not authorize real trading.
