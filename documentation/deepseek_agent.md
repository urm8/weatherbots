# Run the DeepSeek weather paper agent

The standalone `weather_agent` reviews Weatherbots forecasts and records
**simulated YES positions only**. It never calls a broker, places real wagers,
withdraws winnings, or pays/top-ups an API account. Its objective is to measure
whether **realized paper profit** would cover estimated API costs—not to promise
that an agent can fund itself.

The new agent uses Python 3.11+ standard-library modules and does not require the
full forecasting stack. Run commands from the repository root. Existing live
Weatherbots scripts remain separate; running this module does not enable them.

## 1. Try the zero-credential offline demo

```bash
python3 -m weather_agent demo --db /tmp/weather-agent-demo.sqlite
python3 -m weather_agent status --db /tmp/weather-agent-demo.sqlite
```

The demo fabricates a forecast, market, model approval, and usage record. It makes
**no network requests**. Output is labeled synthetic and is not a backtest or
evidence of profitability. Use a separate ledger for research: demo ledgers cannot
fund real API calls. Repeating a market review is rejected rather than duplicating
API spend or paper positions.

## 2. Prepare reviewed market and forecast inputs

The agent accepts one **normalized, human-reviewed snapshot**, not a market title
or arbitrary exchange response. All fields below are required; extra fields are
rejected. This illustrative October snapshot will be rejected once stale—replace
it with current, independently checked data rather than changing timestamps to
make old data look fresh.

```json
{
  "id": "REPLACE-WITH-EXACT-MARKET-TICKER",
  "city": "ny",
  "date": "2026-10-25",
  "quoted_at": "2026-10-25T16:59:00Z",
  "closes_at": "2026-10-26T03:59:00Z",
  "lower_f": 70,
  "upper_f": 71,
  "yes_ask": 0.20,
  "fee_per_contract": 0.01,
  "settlement_source": "NWS_CLI",
  "station": "KNYC",
  "rules_verified": true
}
```

- Prices and fees are **USD per contract**, not cents; a winning contract pays $1.
  Supply a conservative, reviewed all-in fee per contract; it is not fetched from
  an exchange or computed from an official fee schedule.
- Bounds are inclusive **integer Fahrenheit settlement values**. Use `null` for
  one unbounded end, never both. Confirm the real contract uses these semantics
  before setting `rules_verified`; that flag is an assertion, not automated rule
  verification.
- Supported city/station pairs are `ny/KNYC`, `il/KMDW`, `tx/KAUS`, and `fl/KMIA`.
  The target date must be today's date in that city's timezone. Arbitrary stations,
  currencies, settlement sources, and market types are not supported.
- Quote timestamps must be offset-aware, not future-dated, no more than five
  minutes old, and before a future market close.
- Forecast input is `Data/predictions_history.csv`, with at least
  `date,city,run_ts,tmax_predicted,spread_f`. The latest eligible matching row must
  have been generated no later than the quote and no more than two hours ago.
  `run_ts` must be offset-aware. `Data/predictions_latest.csv` lacks that provenance
  and is not accepted. Checked-in September 2026 data is stale for current runs.

There is no live market discovery, orderbook/depth verification, automated NWS
outcome fetching, or historical replay in this initial agent. See the
[research reference and evaluation requirements](prediction_market_research.md).

## 3. Configure a small, separately funded API research budget

Export configuration in your shell or service environment; the standalone module
does **not** automatically load `.env`. Never commit an API key.

```bash
export DEEPSEEK_API_KEY='YOUR-PRIVATE-KEY'
export DEEPSEEK_MODEL='deepseek-flash'
export DEEPSEEK_INPUT_USD_PER_MTOK='0.30'
export DEEPSEEK_CACHE_HIT_USD_PER_MTOK='0.006'
export DEEPSEEK_OUTPUT_USD_PER_MTOK='1.20'

python3 -m weather_agent run \
  --db /tmp/weather-agent-research.sqlite \
  --forecast Data/predictions_history.csv \
  --market /path/to/reviewed-market.json \
  --api-budget 1.00 \
  --paper-bankroll 100 \
  --max-stake 2 \
  --daily-cap 10 \
  --loss-cap 10
```

This command **can incur real DeepSeek charges**, but opens only paper positions.
`--api-budget` is the ledger's total prepaid research-spend cap (default **zero**),
not an instruction to buy credits or proof of your provider balance. Paper profit
never replenishes it. Use provider-side limits too; local estimates cannot enforce
DeepSeek's billing. Ledger limits are immutable: do not rotate ledgers to evade
caps, and account for spending across any separate ledgers yourself.

The example rates are the documented **peak USD per million tokens** for
`deepseek-flash`, checked on 2026-10-25. All rates and the model are explicit inputs;
verify them again before spending. Peak rates conservatively ignore off-peak
discounts. Usage estimates are not provider invoices.

The agent posts bounded JSON to
`https://api.deepseek.com/chat/completions`, explicitly disables thinking, requests
JSON output, and caps completion tokens. The model returns only an approval or
veto with a reason; it cannot change probabilities, budgets, or quantities.
No paid API call was needed for the offline demo.

## 4. Understand decisions and costs

The deterministic baseline uses a normal temperature distribution with a 2°F
uncertainty floor and rejects provider spread above 3°F. For an inclusive integer
bucket `[lo, hi]`, probability is `CDF(hi + 0.5) - CDF(lo - 0.5)` with appropriate
unbounded tails. This is an **uncalibrated modeling assumption**, not proof of a
real market edge.

Before requesting model review, gates enforce bankroll, stake, daily spend, and
worst-case loss/exposure limits; require at least $0.05 per-contract edge after
the supplied fee; and require total expected paper profit to exceed reserved API
cost. Approval cannot bypass those gates. Quotes are checked again after the
response. Positions use the submitted ask without actual fills, depth, or
slippage simulation, so paper P&L can overstate attainable returns.

API accounting distinguishes cached and uncached prompt tokens:

```text
estimated_usd = (cache_miss_tokens * input_rate
               + cache_hit_tokens * cache_hit_rate
               + completion_tokens * output_rate) / 1_000_000
```

The SQLite ledger reserves a conservative request estimate transactionally before
dispatch. It reconciles validated usage, retains costs even if the model's answer
is unusable, and leaves the reservation held if a timeout/interruption or invalid
usage makes spending uncertain. It does not blindly retry: the first request may
already be billable. A duplicate market ID cannot be reviewed again in that
ledger. A held reservation is not confirmation of a provider charge; reconcile
uncertain cases against provider records before any separate recovery.

For reproducibility, `reviews.metadata` stores the non-secret model identifier,
configured rates, forecast and market snapshots, probability, decision timestamp,
token cap, reservation, and validated usage when available. API credentials are
not persisted in this metadata.

## 5. Inspect and settle paper positions

```bash
python3 -m weather_agent status --db /tmp/weather-agent-research.sqlite

# Only after market close and after independently reviewing the official outcome:
python3 -m weather_agent settle \
  --db /tmp/weather-agent-research.sqlite \
  --market-id EXACT-MARKET-TICKER \
  --outcome yes \
  --evidence 'Official NWS CLI publication URL/reference, station, weather date and reviewed outcome'
```

Settlement is a manual paper-ledger operation; the evidence string is recorded,
not fetched or verified. Early or duplicate settlement is rejected. `status`
separates open paper cost, paper cash, realized paper profit, estimated or reserved
API spend, remaining API budget, and uncertain request count.

`paper_net_after_api_usd` is realized paper profit minus API spend/reservations.
`paper_api_coverage_ratio` compares realized paper profit with those costs; it is
`null` when no costs exist. Neither figure credits unrealized positions or creates
spendable money. A ratio above one is **not** an API payment or a profitability
guarantee.

## Official API references

- [Chat completions](https://api-docs.deepseek.com/api/create-chat-completion)
- [JSON output](https://api-docs.deepseek.com/guides/json_mode)
- [Thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [Pricing](https://api-docs.deepseek.com/quick_start/pricing/) (retain the trailing slash)
- [Context caching](https://api-docs.deepseek.com/guides/kv_cache/)
- [Read-only account balance](https://api-docs.deepseek.com/api/get-user-balance/)

The examined reference exposes balance reads, not automatic funding. No payment
or top-up integration is implemented.
