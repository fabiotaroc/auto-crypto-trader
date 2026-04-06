# autotrader

This is an experiment to have the LLM iterate on trading strategies autonomously.

## Setup

To set up a new experiment, work with the user to:

1. **Agree on a run tag**: propose a date-based tag for this run (prefer ISO style, e.g. `2026-04-06`, add a suffix like `-b` if needed). The branch `autotrader/<tag>` must not already exist — this is a fresh run.
2. **Create the branch**: `git checkout -b autotrader/<tag>` from current master.
3. **Read the in-scope files**: The repo is small. Read these files for full context:
  - `backtest.py` — fixed infrastructure: historical data download, replay engine, execution simulation, evaluation harness. Do not modify.
  - `exchange.py` — fixed infrastructure: Kraken live WebSocket feed, shadow execution engine, safety rails. Do not modify. Used only for optional live validation.
  - `strategy.py` — the file you modify. Trading signals, entry/exit logic, position sizing.
4. **Ensure historical data is fresh**: Run `uv run backtest.py --update-data` to download/update BTC/EUR 1-minute candle data from Kraken's public API. This caches to `data/btceur_1m.csv`. Run this at the start of every session to ensure the backtest window includes the latest market data.
5. **Initialize results.tsv safely**: `results.tsv` is a persistent local experiment ledger across runs. If it does not exist, create it with the header row below. If it already exists, preserve its contents and keep appending new rows. Never clear it automatically at the start of a run.
6. **Confirm and go**: Confirm setup looks good.

Once you get confirmation, kick off the experimentation.

## Experimentation

Each experiment **backtests the strategy against months of historical BTC/EUR data** — replaying tens of thousands of 1-minute candles with simulated execution (realistic slippage + fees). A single backtest completes in seconds, giving you fast, deterministic feedback. You launch it simply as: `uv run backtest.py`.

**What you CAN do:**

- Modify `strategy.py` — this is the only file you edit. Everything is fair game: indicators, signal logic, position sizing, risk management, regime detection, ensemble methods. The only constraint is maintaining the `Strategy` class interface (`__init__`, `on_tick`, `on_fill`) and the `Signal` dataclass.

**What you CANNOT do:**

- Modify `backtest.py` or `exchange.py`. They are read-only. They contain the fixed evaluation, execution simulation, and constants.
- Install new packages or add dependencies. You can only use what's already in `pyproject.toml`.
- Modify the evaluation harness. The metrics computed by `backtest.py` are the ground truth.

**The goal is simple: get the highest sharpe_ratio.** The annualized Sharpe ratio measures risk-adjusted returns over the historical backtest period. A Sharpe above 1.0 is good, above 2.0 is strong. The metric accounts for realistic transaction costs (0.26% simulated Kraken taker fee + 0.05% slippage).

**Starting portfolio:** The backtest starts with **€500 cash + 0.001 BTC**. This means you manage a two-asset portfolio: EUR and BTC. You can sell BTC to lock in EUR (profiting from drops), or buy more BTC with EUR (profiting from rises). This is NOT short selling — you're simply managing how much of each asset you hold. The "do nothing" baseline is holding the initial portfolio, which means your Sharpe must beat buy-and-hold.

**Decision cadence:** The strategy's `on_tick()` is called **once every 15 minutes** (every 15th 1-minute candle). You do not control when you are called — the infrastructure decides. However, `tick["history"]` contains ALL 1-minute close prices accumulated so far, so you have full-resolution data for indicator computation. This 15-minute cadence is identical in backtest, shadow, and live modes — your strategy transfers directly to production without parameter changes.

**Important constraints:**

- **Deterministic feedback.** Unlike live trading, the same strategy always produces the same result against the same historical data. This means you can make incremental changes and reliably tell whether they helped or hurt.
- **Data is 1-minute, decisions are 15-minute.** The cached data contains 1-minute OHLC candles. Your `tick["history"]` list grows by 15 entries between each call to `on_tick()`. Indicator windows should be calibrated in minutes (e.g. a 240-element lookback in history covers 4 hours). You get ~96 decision points per day, ~8640 over 90 days.
- **Train/test split.** The backtest automatically splits data 80/20 — the first 80% is train (what sharpe_ratio reports), the last 20% is test (what sharpe_test reports). You optimize sharpe_ratio (train). If sharpe_test diverges significantly below sharpe_ratio, the strategy is overfitting to historical patterns.
- **Overfitting is the main risk.** With deterministic backtesting, it's easy to fit noise. Prefer strategies with a clear theoretical basis (momentum, mean-reversion, volatility breakout) over heavily tuned magic numbers. Simpler strategies generalize better.
- **Fees matter.** The backtest simulates 0.26% taker fees (Kraken default) and 0.05% slippage. A strategy whose edge is eaten by fees will score poorly. The 15-minute cadence naturally encourages strategies that capture larger moves rather than scalping.
- **Position limits.** The engine enforces €100 max position. Strategies that exceed this get clamped.
- **Minimum activity.** A strategy that produces fewer than 20 round-trip trades (buy+sell pairs) over the 90-day backtest is invalid. If the market is hostile, find a way to trade it profitably — rotating between BTC and EUR based on conditions — do not opt out. "Do nothing" or "hold forever" are never acceptable final strategies.

**The first run**: Your very first run should always be to establish the baseline, so you will run the experiment as is.

## Output format

Once the script finishes it prints a summary like this:

```
---
sharpe_ratio:     1.85
sharpe_test:      1.42
profit_factor:    2.150000
total_pnl_eur:    47.30
num_trades:       312
max_drawdown_eur: 8.20
win_rate:         0.583
avg_trade_pnl:    0.1517
time_in_market:   0.41
backtest_days:    90.0
train_days:       72.0
test_days:        18.0
mode:             backtest
```

You can extract the key metric from the log file:

```
grep "^sharpe_ratio:" run.log
```

## Logging results

When an experiment is done, log it to `results.tsv` (tab-separated, NOT comma-separated — commas break in descriptions). This file is a persistent local history across runs, so always append and include the active `run_tag`.

The TSV has a header row and 8 columns:

```
run_tag	commit	sharpe_ratio	sharpe_test	pnl_eur	num_trades	status	description
```

1. `run_tag` for the current session/branch (date-based, e.g. `2026-04-06`)
2. git commit hash (short, 7 chars)
3. sharpe_ratio (train) achieved (e.g. 1.85) — use 0.00 for crashes
4. sharpe_test achieved (e.g. 1.42) — use 0.00 for crashes
5. total P&L in EUR (e.g. 47.30) — use 0.00 for crashes
6. num_trades executed
7. status: `keep`, `discard`, or `crash`
8. short text description of what this experiment tried

Example:

```
run_tag	commit	sharpe_ratio	sharpe_test	pnl_eur	num_trades	status	description
2026-04-06	a1b2c3d	0.00	0.00	0.00	0	keep	baseline (momentum crossover)
2026-04-06	b2c3d4e	1.85	1.42	47.30	312	keep	add RSI filter with 60-min window
2026-04-06	c3d4e5f	2.10	0.30	52.10	890	discard	aggressive scalping (overfit — train/test diverge)
2026-04-06	d4e5f6g	0.00	0.00	0.00	0	crash	division by zero in volatility calc
```

## The experiment loop

The experiment runs on a dedicated branch (e.g. `autotrader/2026-04-06`).

LOOP FOREVER:

1. Look at the git state: the current branch/commit we're on
2. Tune `strategy.py` with an experimental idea by directly hacking the code.
3. git commit (temporary unless the run is later marked `keep`)
4. Run the experiment: `uv run backtest.py > run.log 2>&1` (redirect everything — do NOT use tee or let output flood your context)
5. Read out the results: `grep "^sharpe_ratio:\|^sharpe_test:\|^num_trades:" run.log`
6. If the grep output is empty, the run crashed. Run `tail -n 50 run.log` to read the Python stack trace and attempt a fix. If you can't get things to work after more than a few attempts, give up.
7. Record the results in the tsv with the active `run_tag` (NOTE: do not commit the results.tsv file, leave it untracked by git)
8. **Keep/discard decision** (apply in order):
  a. If the strategy crashed → log as `crash`, revert.
  b. If num_trades < 30 → log as `discard`. The strategy is too passive. Keep iterating.
  c. If sharpe_ratio (train) is good but sharpe_test is much lower (e.g. train > 2.0 but test < 0.5) → log as `discard` (overfitting).
  d. If there is no prior `keep` in this `run_tag` with `num_trades >= 30`, then the first run with `num_trades >= 30` becomes the active baseline → `keep`.
  e. Otherwise, if sharpe_ratio improved over the current best active `keep` within the `run_tag` → `keep`, advance the branch.
  f. Otherwise, if sharpe_ratio is within 0.50 of the current best active `keep` and either sharpe_test or pnl_eur improved → `keep`.
  g. Otherwise → `discard`, git reset back to where you started.
9. Only `keep` commits stay on the branch. `discard` and `crash` commits are temporary checkpoints used for logging; after recording the result, reset them off the branch tip/history before the next experiment.
10. A Sharpe of 0.00 with 0 trades is **never** a valid "keep". That means you haven't found a strategy — keep searching. The starting portfolio already has BTC exposure, so even doing nothing produces a non-zero (likely negative in a downtrend) Sharpe. You must beat that.

The idea is that you are a completely autonomous trading researcher trying things out. You manage a portfolio of EUR and BTC — selling BTC when you expect drops, buying BTC when you expect rises. If they work, keep. If they don't, discard. And you're advancing the branch so that you can iterate.

**Speed**: Each backtest takes seconds (not minutes). You can run dozens of experiments per hour. Use this speed to explore broadly — try many different indicator families, timeframes, and approaches.

**Crashes**: If a run crashes (a bug in strategy.py), use your judgment: fix obvious bugs and re-run. If the idea itself is broken, log "crash", revert, and move on.

**Data freshness**: At the start of each session, run `uv run backtest.py --update-data` to ensure the cache includes the latest market data up to now. The backtest window should always end at the present.

**Run-to-run comparability**: Because each session refreshes data and the 90-day backtest window slides forward, results from different `run_tag`s are useful history but are not perfectly comparable. Use `run_tag` to compare experiments within the same run first; use cross-run history mainly for auditability and idea tracking.

**Live validation (optional)**: Periodically, you can validate the current best strategy against live market data using `uv run exchange.py --duration 120 > live.log 2>&1`. This runs a 2-minute live shadow cycle against real-time Kraken prices. It's a sanity check, not the primary metric — live results will differ from backtest due to different market conditions.

**NEVER STOP**: Once the experiment loop has begun (after the initial setup), do NOT pause to ask the human if you should continue. The human might be asleep or away. You are autonomous. If you run out of ideas, think harder — try different indicator families, different timeframes, different position sizing approaches, ensemble methods, regime detection. The loop runs until the human interrupts you, period.

## Strategy design space

You manage a EUR+BTC portfolio. The core question each 15-minute decision point is: **should I be holding more BTC or more EUR right now?**

Some directions to explore:

**Signal generation:** Moving average crossovers (SMA/EMA/WMA), momentum/rate-of-change, mean reversion (Bollinger bands, z-score), RSI, MACD, stochastic oscillator, volatility breakout (ATR-based), price action patterns, spread changes.

**Portfolio rotation:** Sell BTC → EUR when bearish signals fire, buy BTC with EUR when bullish signals fire. This lets you profit in both up and down markets without short selling.

**Position sizing:** Fixed size, volatility-scaled (Kelly, ATR-based), confidence-weighted. Partial rotations (sell half, keep half) vs. all-or-nothing.

**Risk management:** Stop-loss/take-profit, trailing stops, time-based exits, drawdown circuit breakers.

**Regime detection:** Trend vs. range-bound, volatility regime switching, multi-timeframe analysis.

**Combinations:** Signal ensembles with voting, signal filtering (only trade when multiple indicators agree), adaptive parameters.

**Important note on timescale:** `on_tick()` is called every 15 minutes, but `tick["history"]` contains 1-minute close prices. A 240-element lookback in history covers 4 hours. A "fast" moving average might be 20-60 minutes, a "slow" one 120-480 minutes. You get ~96 decisions per day.