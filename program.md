# autotrader

This is an experiment to have the LLM iterate on trading strategies autonomously.

## Setup

To set up a new experiment, work with the user to:

1. **Agree on a run tag**: propose a tag based on today's date (e.g. `mar30`). The branch `autotrader/<tag>` must not already exist — this is a fresh run.
2. **Create the branch**: `git checkout -b autotrader/<tag>` from current master.
3. **Read the in-scope files**: The repo is small. Read these files for full context:
   - `BRAINSTORM.md` — full design document and rationale.
   - `exchange.py` — fixed infrastructure: Kraken WebSocket feed, shadow execution engine, evaluation harness, safety rails. Do not modify.
   - `strategy.py` — the file you modify. Trading signals, entry/exit logic, position sizing.
4. **Initialize results.tsv**: Create `results.tsv` with just the header row. The baseline will be recorded after the first run.
5. **Confirm and go**: Confirm setup looks good.

Once you get confirmation, kick off the experimentation.

## Experimentation

Each experiment connects to Kraken's public WebSocket for real-time BTC/EUR prices and runs a **fixed 10-minute trading window** in shadow mode (simulated execution — no real money). You launch it simply as: `uv run exchange.py`.

**What you CAN do:**
- Modify `strategy.py` — this is the only file you edit. Everything is fair game: indicators, signal logic, position sizing, risk management, regime detection, ensemble methods. The only constraint is maintaining the `Strategy` class interface (`__init__`, `on_tick`, `on_fill`) and the `Signal` dataclass.

**What you CANNOT do:**
- Modify `exchange.py`. It is read-only. It contains the fixed evaluation, shadow engine, safety rails, and constants.
- Install new packages or add dependencies. You can only use what's already in `pyproject.toml`.
- Modify the evaluation harness. The `compute_metrics` function in `exchange.py` is the ground truth metric.

**The goal is simple: get the highest profit_factor.** A profit factor above 1.0 means the strategy is profitable (gross profit exceeds gross loss). Above 1.75 is considered strong for short-term strategies. The metric accounts for realistic transaction costs (0.26% simulated Kraken taker fee + 0.05% slippage).

**Important constraints:**
- **Minimum 3 trades.** If fewer than 3 round-trip trades complete in 10 minutes, profit_factor is scored as 0.0. The strategy must actually trade.
- **Non-stationary market.** Unlike a fixed validation set, the market is live and changing. Two identical strategies run 10 minutes apart may score differently due to volatility, trend, or news. Accept the noise and rely on trends over many experiments.
- **Fees matter.** The shadow engine simulates 0.26% taker fees (Kraken default). A strategy whose edge is eaten by fees will score poorly. Discover fee-aware strategies that trade less frequently but more profitably.
- **Position limits.** The shadow engine enforces €100 max position, €10 max loss per cycle, and 50 max trades. Strategies that hit these limits get force-closed.

**The first run**: Your very first run should always be to establish the baseline, so you will run the experiment as is.

## Output format

Once the script finishes it prints a summary like this:

```
---
profit_factor:    2.150000
total_pnl_eur:    3.42
num_trades:       11
max_drawdown_eur: 1.05
win_rate:         0.636
avg_trade_pnl:    0.3109
sharpe_ratio:     1.23
time_in_market:   0.52
cycle_seconds:    600.0
mode:             shadow
```

You can extract the key metric from the log file:

```
grep "^profit_factor:" run.log
```

## Logging results

When an experiment is done, log it to `results.tsv` (tab-separated, NOT comma-separated — commas break in descriptions).

The TSV has a header row and 5 columns:

```
commit	profit_factor	pnl_eur	num_trades	status	description
```

1. git commit hash (short, 7 chars)
2. profit_factor achieved (e.g. 1.850000) — use 0.000000 for crashes
3. total P&L in EUR (e.g. 1.23) — use 0.00 for crashes
4. status: `keep`, `discard`, or `crash`
5. short text description of what this experiment tried

Example:

```
commit	profit_factor	pnl_eur	num_trades	status	description
a1b2c3d	0.000000	0.00	0	keep	baseline (momentum crossover)
b2c3d4e	1.850000	1.23	12	keep	add RSI filter
c3d4e5f	0.620000	-2.10	45	discard	aggressive scalping (overtrading)
d4e5f6g	0.000000	0.00	0	crash	division by zero in volatility calc
```

## The experiment loop

The experiment runs on a dedicated branch (e.g. `autotrader/mar30`).

LOOP FOREVER:

1. Look at the git state: the current branch/commit we're on
2. Tune `strategy.py` with an experimental idea by directly hacking the code.
3. git commit
4. Run the experiment: `uv run exchange.py > run.log 2>&1` (redirect everything — do NOT use tee or let output flood your context)
5. Read out the results: `grep "^profit_factor:\|^total_pnl_eur:" run.log`
6. If the grep output is empty, the run crashed. Run `tail -n 50 run.log` to read the Python stack trace and attempt a fix. If you can't get things to work after more than a few attempts, give up.
7. Record the results in the tsv (NOTE: do not commit the results.tsv file, leave it untracked by git)
8. If profit_factor improved (higher), you "advance" the branch, keeping the git commit
9. If profit_factor is equal or worse, you git reset back to where you started

The idea is that you are a completely autonomous trading researcher trying things out. If they work, keep. If they don't, discard. And you're advancing the branch so that you can iterate.

**Key differences from standard ML experimentation:**
- **Market is non-stationary.** A strategy that works in the morning may fail at night. If a strategy has a strong theoretical basis and shows marginal improvement, you might keep it even with a noisy result. Over dozens of cycles, consistently good strategies will float to the top.
- **Must close positions.** Each cycle starts and ends flat. The exchange forces this automatically.
- **Crashes are safe.** In shadow mode, a crash just loses virtual state — no real consequences.

**Timeout**: Each experiment should take ~10 minutes total (+ a few seconds for WebSocket connection). If a run exceeds 15 minutes, kill it and treat it as a failure (discard and revert).

**Crashes**: If a run crashes (a bug in strategy.py), use your judgment: fix obvious bugs and re-run. If the idea itself is broken, log "crash", revert, and move on.

**NEVER STOP**: Once the experiment loop has begun (after the initial setup), do NOT pause to ask the human if you should continue. The human might be asleep or away. You are autonomous. If you run out of ideas, think harder — try different indicator families, different timeframes, different position sizing approaches, ensemble methods, regime detection. The loop runs until the human interrupts you, period.

## Strategy design space

Some directions to explore:

**Signal generation:** Moving average crossovers (SMA/EMA/WMA), momentum/rate-of-change, mean reversion (Bollinger bands, z-score), RSI, MACD, stochastic oscillator, volatility breakout (ATR-based), price action patterns, spread changes.

**Position sizing:** Fixed size, volatility-scaled (Kelly, ATR-based), confidence-weighted.

**Risk management:** Stop-loss/take-profit, trailing stops, time-based exits, drawdown circuit breakers.

**Regime detection:** Trend vs. range-bound, volatility regime switching, multi-timeframe analysis.

**Combinations:** Signal ensembles with voting, signal filtering (only trade when multiple indicators agree), adaptive parameters.
