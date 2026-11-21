# Full-position management

An explicit enrollment covers one account scope, market, and ticker. It covers all
shares of that instrument in that account, regardless of purchase channel. Fresh
broker holdings and sellable quantities reflect manual trades. Strategy evidence
and the existing watcher guards continue to govern other positions.

## Enrollment and operation

The policy requires an explicit `LATCH_EXIT` selection. A price at or below the
threshold latches the policy when the account holds shares. Once latched, future
manual repurchases remain eligible for exit even after the price recovers. A flat
balance preserves the latch and the stop threshold. An existing enrollment rejects
a different threshold or latch policy.

Use the trusted integration boundary in `subscriber.main(managed_gateway=…)`
or `TradeDispatcher(managed_gateway=…)`. Enroll through the dispatcher's
`managed_position_book.enroll(account_scope, market, ticker, stop_loss,
latch_policy="LATCH_EXIT")` using the SHA-256 digest of the canonical account key.
Initialize enrollment before starting order workers. Subscriber polling uses the
enabled stop-loss watcher. The dispatcher also checks the selected trading
environment, active account, open market, and automatic-trading setting.

Processes handling the same account must share the PRISM runtime directory and
its locks, use the common execution ledger, and keep execution deduplication enabled. The managed book remains part of the ordinary dispatcher fence even
in a process without a managed gateway. Separate ledgers or processes that skip
execution deduplication cannot participate in managed cutover. Ordinary PRISM SELLs for an enrolled
account and instrument are deferred. Existing strategy orders with uncertain
submission, and accepted or opaque legacy SELLs without terminal evidence,
block cutover. New SELL claims retain verified account scope when available
and otherwise retain instrument scope. Preserve the managed book on restart.

## Evidence and order handling

The gateway requires a complete, fresh, account-bound inventory, sellable
quantity, quote and order inventory covering open orders and reservations.
An open manual order or divergent holding and sellable quantities pauses the
exit. A second snapshot immediately before the cash API must still support the
full reserved quantity. The US adapter uses the validated quote as its limit.

The controller persists the exact quantity and a submission fence before the
cash call. A confirmed local preflight pause can retry on the next snapshot.
After an ambiguous submission or crash, the reservation remains in force
until matched broker evidence resolves it. A dated receipt records acceptance.
Confirmed fill IDs and explicit cancellation quantities must account for the
original order. Terminal confirmation forces another holdings read before
any subsequent exit. The inventory timestamp must be strictly newer than
terminal evidence. An unknown submission without an order identifier stays
paused when no authoritative correlation is available.

## KIS capability limits

The shipped `ManagedBrokerGateway.submit_exact_sell` now rejects **all real-account**
automatic SELL requests before any cash order call. A caller-supplied snapshot
marked `complete=true` or an injected order reader cannot establish atomic
exclusion of concurrent MTS/HTS/PRISM orders. This is a deliberate fail-closed
restriction, not evidence that live automatic stop-loss is supported.
Offline demo fixtures continue exercising the full order-state transitions.
A future live implementation requires independently verified broker-side
cross-channel atomic execution or reservation; do not remove the guard based
only on extra polling or an external provider's self-attestation.

The stock CLI has no complete production provider wired by default. A trusted
gateway needs both complete open-order coverage and recoverable, dated SELL
execution evidence before submission. The documented native KIS queries provide
cash holdings and bounded pages of open orders, with additional reservation
queries using date windows. They do not establish all externally entered
commitments, and the documented history examples lack the execution identity
and cancellation detail needed for recovery. The existing quote methods do
not expose the timestamp needed by the native snapshot contract. Native cash
examples report an order number and time of day without a broker order date.
That receipt cannot establish a dated order identity for automatic recovery.
The native
gateway pauses at these boundaries and the demo open-order path is unsupported.

The KIS real-time notification formats mix order and execution events. The gateway
accepts scoped invalidation hints. A WebSocket transport is not wired into this
runtime. Notifications never release a reservation.
[KIS domestic notification](https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock/ccnl_notice)
· [KIS overseas notification](https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/ccnl_notice)

[KIS domestic open orders](https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock/inquire_psbl_rvsecncl)
· [KIS overseas open orders](https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/inquire_nccs)
· [KIS domestic order history](https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock/inquire_daily_ccld)
· [KIS overseas order history](https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/inquire_ccnl)

MTS and HTS do not share PRISM's locks. A manual request can reach KIS between
the final query and the automated submission. Preventing that race requires
broker-side conditional order placement or an atomic reservation covering all
channels. Offline tests verify PRISM's checks and local order serialization.
