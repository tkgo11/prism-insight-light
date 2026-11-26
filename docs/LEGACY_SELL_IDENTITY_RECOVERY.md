# Legacy SELL identity quarantine

A historical execution key can refer to a deleted account selector. If its
canonical routing identity cannot be reconstructed, the ledger holds new SELLs
rather than assume a different signal or account. An old record with no scope
holds all SELLs; a record with scope holds SELLs for that market and ticker
across accounts because the recorded account is not proof of the account used.
This may indefinitely prevent legitimate exits, including protective
exits. Do not interpret a held order as submitted or filled. Escalate the
exposure to an operator using an independent broker workflow.

New multi-account claims record that the identity is canonical. A modern
single-account claim records its canonical identity separately. A possible
submission also holds distinct signals for the same market, ticker and account:
an acknowledgement, UNKNOWN, or an executed status is not evidence that an
order filled or ceased reserving shares. Other accounts and instruments remain
separate when the modern account identity is known. Existing multi-account
records without that marker remain ambiguous.

Escalate the held record for independent operator reconciliation. The recorded
scope and selector hashes alone do not prove which account actually received
an order: some historic versions did not pin the broker account after creating
the claim. A safe release would require verified historical implementation and
configuration provenance together with authoritative broker outcome evidence;
those capabilities are not currently established. No automated release is
provided. Keep records and evidence intact; do not alter the ledger to force
execution. Manual access to a broker is an external operational decision, not
an action performed by this project or its offline tests.
