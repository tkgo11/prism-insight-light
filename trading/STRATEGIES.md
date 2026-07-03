# Trading Strategy Guide

This file explains every strategy in `trading/strategies` in simple words.

Think of a **strategy** like a rule that tells the trading bot what to do when a signal arrives.
A signal can say things like:

- **BUY**: buy a stock.
- **SELL**: sell a stock.
- **EVENT**: remember that something important happened.

Only one strategy is selected by `signal_strategy.name` in `trading/config/kis_devlp.yaml`.
If `name` is empty, the bot uses the normal old behavior.

## Quick list

| Strategy name | Easy idea | Works with |
| --- | --- | --- |
| `balance_split` | Split the cash into equal pieces and buy with one piece. | BUY |
| `score_weighted` | Buy more when the signal score is stronger. | BUY |
| `risk_bracket` | Decide the buy size by how much money you are willing to risk. | BUY |
| `profit_ladder` | Sell in steps as profit gets bigger, like climbing a ladder. | SELL |
| `stop_loss_sell` | Sell at the stop-loss price sent by Pub/Sub. | SELL |
| `limit_buffer` | Move the limit price a little to help the order price. | BUY and SELL |
| `cooldown` | Wait before trading the same thing again. | Usually BUY |
| `event_risk_off` | Remember danger events and block or shrink buys for a while. | EVENT and BUY |

## How to choose a strategy

In `trading/config/kis_devlp.yaml`, set:

```yaml
signal_strategy:
  name: "stop_loss_sell"
```

The example config defaults to `stop_loss_sell` so SELL signals use their Pub/Sub `stop_loss` as the limit price. Replace `stop_loss_sell` with another strategy when you want different behavior.
Each strategy also has its own settings under `signal_strategy`.
The example config file shows the available setting names, but this guide explains what they mean.


## `stop_loss_sell`

### In one sentence

`stop_loss_sell` sells at the stop-loss price included in the incoming Pub/Sub signal.

### Kid-friendly example

You tell the bot, "If this stock falls to 180, sell it there."
When a SELL signal arrives with `stop_loss: 180`, the bot sends the sell order with 180 as the limit price.

### What it does

- It only handles **SELL** signals.
- It prefers `stop_loss` over `price` for the sell limit price.
- For US stop-loss SELL signals where `price` has already moved below `stop_loss`, it uses the lower signal `price` so the KIS limit order is marketable instead of waiting for a recovery.
- If `stop_loss` is missing and `fallback_to_signal_price` is `true`, it uses the signal `price` instead.
- If neither usable price exists, it rejects the strategy execution instead of sending an unpriced sell.

### Main setting

- `fallback_to_signal_price`: whether to use `price` when `stop_loss` is missing, or when a US SELL arrives after `price` has already crossed below `stop_loss`.
  - Default: `true`.

### When it is useful

Use this when Pub/Sub already calculates a stop-loss price and you want the bot's default SELL behavior to honor that stop-loss.

## `balance_split`

### In one sentence

`balance_split` buys with one equal piece of the cash currently available.

### Kid-friendly example

You have 10 cookies and 2 friends.
If `split_count` is `2`, you only give away half of the cookies now.
The other half stays for later.

### What it does

- It only handles **BUY** signals.
- It checks how much cash is available in the account.
- It divides that cash by `split_count`.
- It buys using that divided amount.

### Main setting

- `split_count`: how many equal pieces to split your available cash into.
  - Example: `2` means use about half the cash.
  - Example: `4` means use about one quarter of the cash.

### When it is useful

Use this when you do not want one BUY signal to spend all your cash.

## `score_weighted`

### In one sentence

`score_weighted` buys a small, medium, or large amount depending on the signal's `buy_score`.

### Kid-friendly example

Imagine a teacher gives a stock idea a score.
A score of 60 gets a small snack.
A score of 90 gets a big snack.
Better score, bigger buy.

### What it does

- It only handles **BUY** signals.
- It reads `buy_score` from the signal.
- If the score is too low, it does not buy.
- If the score is high enough, it multiplies the base buy amount by a weight.

### Main settings

- `base_amount_krw`: base buy amount for Korean stocks.
- `base_amount_usd`: base buy amount for U.S. stocks.
- `min_score`: the minimum score needed before buying.
- `score_bands`: score levels and their weights.
  - Example: `60: 0.25` means buy 25% of the base amount when the score is at least 60.
  - Example: `90: 1.0` means buy 100% of the base amount when the score is at least 90.

### When it is useful

Use this when your signal has a confidence score and you want stronger ideas to get more money.

## `risk_bracket`

### In one sentence

`risk_bracket` chooses the buy size by looking at how much you could lose if the stop loss is hit.

### Kid-friendly example

You are playing a game and say, "I can lose only 1 sticker on this round."
The strategy checks the entry price and stop-loss price, then decides how many shares fit that sticker limit.

### What it does

- It only handles **BUY** signals.
- It needs an entry `price`.
- It usually needs `stop_loss` too.
- It calculates the gap between the buy price and the stop-loss price.
- If `require_stop_loss` is `false` and the signal omits `stop_loss`, the strategy sizes the trade as if the stop loss were `0`, which can make the calculated position very small or reject the trade.
- It uses your risk budget to decide the buy amount.
- It can save bracket information like entry price, stop loss, and target price.

### Main settings

- `risk_amount_krw`: how much Korean won you are willing to risk.
- `risk_amount_usd`: how many U.S. dollars you are willing to risk.
- `max_position_amount_krw`: biggest Korean-stock position allowed by this strategy.
- `max_position_amount_usd`: biggest U.S.-stock position allowed by this strategy.
- `require_stop_loss`: whether the signal must include `stop_loss`; even when this is `false`, include a meaningful `stop_loss` if you want normal stop-loss-based sizing.
- `require_target_price`: whether the signal must include `target_price`.

### When it is useful

Use this when you care more about "how much can I lose if I am wrong?" than "how much should I spend?"

## `profit_ladder`

### In one sentence

`profit_ladder` sells different portions as profit reaches different levels.

### Kid-friendly example

Imagine climbing stairs.
At the first stair, you sell a little.
At the next stair, you sell more.
At the top stair, you may sell all.

### What it does

- It only handles **SELL** signals.
- It reads `profit_rate` from the signal when available.
- It chooses a sell fraction from `profit_bands`.
- It can sell everything for important exit reasons like stop loss or risk off when no profit band overrides that amount.
- If a SELL signal has both a `full_exit_reasons` reason and `profit_rate`, the matching `profit_bands` value decides the final sell fraction.

### Main settings

- `profit_bands`: profit levels and how much to sell.
  - Example: `5: 0.25` means sell 25% when profit is at least 5%.
  - Example: `20: 1.0` means sell 100% when profit is at least 20%.
- `stop_loss_sell_percent`: how much to sell when the reason is `stop_loss`.
- `default_sell_percent`: how much to sell if no special band or reason applies.
- `full_exit_reasons`: reasons that start as a whole-position sell; if `profit_rate` is also present, the profit ladder can replace this with a partial sell fraction from `profit_bands`.

### When it is useful

Use this when you want to take profit step by step instead of selling everything at once.

## `limit_buffer`

### In one sentence

`limit_buffer` changes the limit price a tiny bit before placing the order.

### Kid-friendly example

If a toy costs 100 coins and you really want to buy it, you might offer 100.1 coins.
If you want to sell a toy, you might accept 99.9 coins.
That tiny change is the buffer.

### What it does

- It handles **BUY** and **SELL** signals.
- It requires the signal to include `price`.
- For BUY, it can raise the limit price by a small percent.
- For SELL, it can lower the limit price by a small percent.
- It rounds prices for U.S. and Korean markets.
- For Korean stocks, tick rounding happens after the buffer is added or subtracted, so a small buffer can round back to the original price when `kr_tick_rounding` is larger than the buffered price change.

### Main settings

- `buy_buffer_percent`: how much to raise the BUY limit price.
- `sell_buffer_percent`: how much to lower the SELL limit price.
- `us_price_decimals`: how many decimal places to keep for U.S. prices.
- `kr_tick_rounding`: how to round Korean stock prices by tick size; choose a buffer large enough to survive this rounding if you need the limit price to move.

### When it is useful

Use this when you want limit orders but also want a small cushion so they are more likely to fill.

## `cooldown`

### In one sentence

`cooldown` stops the bot from trading the same thing again too soon.

### Kid-friendly example

After eating candy, a parent says, "Wait one hour before eating more candy."
That waiting time is the cooldown.

### What it does

- It usually protects **BUY** signals, but it can be configured for other signal types.
- It checks whether the same key traded recently.
- If the key is still cooling down, it rejects the trade.
- If enough time has passed, it lets the trade happen and records it.

### Main settings

- `window_minutes`: how long to wait before allowing the same key again.
- `apply_to_signal_types`: signal types protected by cooldown, such as `BUY`.
- `scope`: what counts as "the same thing".
  - `market_ticker` uses market, ticker, and signal type together.
  - `ticker` uses only the ticker.

### When it is useful

Use this to avoid repeated orders caused by duplicate signals or noisy alerts.

## `event_risk_off`

### In one sentence

`event_risk_off` remembers danger events and blocks or shrinks BUY orders while the danger is fresh.

### Kid-friendly example

If the playground is wet, the teacher writes "wet playground" on the board.
Until the note is old, kids cannot run there, or they must be extra careful.

### What it does

- It handles **EVENT** signals and **BUY** signals.
- When it sees a risk-off event, it records it.
- Later BUY signals for matching market/ticker can be blocked.
- If configured, BUY signals can be made smaller instead of fully blocked.

### Main settings

- `risk_off_event_types`: event names that mean danger, such as `RISK_OFF`.
- `risk_off_window_minutes`: how long the danger note stays active.
- `buy_size_multiplier`: how much to multiply BUY size while risk-off is active.
  - `0.0` means block the BUY.
  - `0.5` means use half of the explicit `buy_amount` in the signal.
  - If the BUY signal does not include `raw.buy_amount`, the multiplier cannot shrink the broker/config default order size; it passes no custom buy amount.

### When it is useful

Use this when news, market stress, or another event should temporarily stop new buying.

## Adding a new strategy

When developers add a new strategy in `trading/strategies`, they must also update this file.
Users should be able to open this guide and understand every available strategy without reading Python code.
