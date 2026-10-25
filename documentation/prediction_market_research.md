# Prediction-market research reference

The repository [Jon-Becker/prediction-market-analysis](https://github.com/Jon-Becker/prediction-market-analysis)
is included as a **research-only Git submodule** at
`references/prediction-market-analysis`, pinned to
`57fbd28b4b9158623900635158ca6f9205668845`.

```bash
git submodule update --init references/prediction-market-analysis
git submodule status references/prediction-market-analysis
```

The paper agent does not import or execute the submodule. Its dependencies,
indexers, datasets, and analyses are not installed or run by the agent. The
reference retains its own MIT license. Its README describes a **36 GiB compressed**
dataset: `make setup` is a separate, substantial download, not part of this setup.
No dataset download or historical profitability analysis was performed for this
integration.

## What the inspected reference supports

| Inspected source | Useful research idea | Limit before using it for weather trading |
| --- | --- | --- |
| [Data schemas](../references/prediction-market-analysis/docs/SCHEMAS.md) | Kalshi market metadata, resolutions, trade timestamps, and prices | Trade prices are in **cents**; the paper agent accepts **USD**. A historical execution is not a currently executable quote. |
| [Win rate by price](../references/prediction-market-analysis/src/analysis/kalshi/win_rate_by_price.py) | Compare realized frequency with implied price; inspect calibration by price | This aggregates maker and taker positions across resolved markets, not a weather-only out-of-sample forecast evaluation. |
| [YES versus NO EV](../references/prediction-market-analysis/src/analysis/kalshi/ev_yes_vs_no.py) | For a $1 binary payout, gross EV per contract is `probability - price_usd` | Empirical historical win rate is not a forecast available at entry. The inspected calculation does not deduct this agent's API costs or execution costs. |
| [Maker versus taker returns](../references/prediction-market-analysis/src/analysis/kalshi/maker_vs_taker_returns.py) | Separate liquidity-provider and liquidity-taker behavior | Aggregate historical returns do not imply that this agent can obtain a maker fill, capture a spread, or reproduce an edge. |
| [Returns by hour](../references/prediction-market-analysis/src/analysis/kalshi/returns_by_hour.py) | Evaluate time-of-day effects | The inspected SQL extracts the hour directly from `created_time`, despite ET labels. Verify stored timestamp timezone and convert explicitly before interpreting city-local timing. |

These are source-code observations, **not reproduced empirical findings**. The
reference's broad-market statistics do not establish that Weatherbots or DeepSeek
can generate profitable weather trades.

## Required evaluation before considering live execution

1. **Freeze a chronological, weather-only dataset.** Record dataset revision,
   market ticker, target local weather date, settlement station, rules revision,
   forecast generation time, quote observation time, and official outcome source.
   Check market snapshots for duplicate tickers before joins; repeated metadata
   snapshots must not multiply trade rows.
2. **Join as of the decision.** Use only forecast rows with `run_ts` no later than
   the decision, and market observations actually available then. `_fetched_at`
   measures collection time, not proof that a later snapshot's prices were
   available earlier. Keep final outcomes strictly in evaluation labels.
3. **Preserve exact settlement semantics.** Match the market's specified NWS
   station, local date, daily high, temperature unit, integer rounding/bucket
   rules, publication/revision policy, and official settlement. A nearby weather
   provider or generic city maximum is not interchangeable with settlement truth.
4. **Model execution conservatively.** Use the executable side of the book, fees,
   spread, depth, partial fills, latency, and slippage. Do not treat a last trade
   or aggregate market price as a guaranteed fill. The initial paper agent takes
   reviewed snapshots; it does not reconstruct historical books.
5. **Calibrate chronologically.** Fit forecast uncertainty and any bias correction
   on past data only. Run walk-forward validation with a held-out period and
   report sample count, Brier score/log loss, calibration, drawdown, and net P&L.
   Compare against the deterministic Weatherbots baseline and an always-skip
   policy; evaluate whether paid model review improves results after costs.
6. **Account for every cost and rejection.** Include usage-based API estimates,
   reserved/uncertain request spend, failed requests, rejected suggestions, and
   model vetoes—not just winning positions. Report out-of-sample confidence
   intervals and correlated city/day exposure. Do not tune on the test period.

Weatherbots' README already documents a corrected label-leakage issue: late-day
forecasts were once evaluated as if known at the decision time. The integration
therefore reads timestamped `Data/predictions_history.csv`, not the untimestamped
latest snapshot. The checked-in September 2026 snapshots are not fresh inputs
for present-day paper decisions.

## Current boundary

The submodule is reference material, not a live exchange adapter, historical
orderbook replay engine, or a validated strategy. The new agent is **paper-only**;
no real wagers, payouts, withdrawals, or API payments are made. See the
[DeepSeek paper-agent guide](deepseek_agent.md) for its operating contract.
