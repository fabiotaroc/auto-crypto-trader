# autotrader: autonomous trading strategy research

## The Idea

Adapt the [autoresearch](https://github.com/karpathy/autoresearch) feedback loop for algorithmic trading. Instead of an AI agent iterating on a neural network training script to minimize `val_bpb`, the agent iterates on a **trading strategy script** to maximize **Sharpe ratio** (annualized risk-adjusted return). The agent backtests each strategy variant against months of historical BTC/EUR data from [Kraken](https://docs.kraken.com/api/), evaluates the result in seconds, keeps or discards, and repeats.

The backtesting loop uses historical 1-minute OHLC candles downloaded from Kraken's public API — no API key needed. Execution is simulated with realistic slippage and fees. The agent can also validate strategies against live market data via a separate shadow-mode engine. Once the agent has iterated enough to find consistently profitable strategies, we flip the switch to live.

You wake up in the morning to a log of strategy experiments and (hopefully) a profitable algorithm.

---

## Mapping autoresearch → autotrader

| autoresearch | autotrader | notes |
|---|---|---|
| `train.py` — model, optimizer, training loop | `strategy.py` — trading logic, signals, position sizing | **the one file the agent edits** |
| `prepare.py` — data prep, eval, constants | `backtest.py` — historical data, replay engine, evaluation harness | **read-only, primary evaluation** |
| — | `exchange.py` — Kraken live WebSocket feed, shadow execution engine | **read-only, live validation** |
| `program.md` — agent instructions | `program.md` — agent instructions (adapted for trading) | **edited by human** |
| `val_bpb` (lower is better) | Sharpe ratio (higher is better), plus secondary metrics | the ground-truth fitness metric |
| 5-min fixed training budget | seconds (historical backtest over 90 days of data) | deterministic, fast iteration |
| single NVIDIA GPU | no GPU needed — just internet for data download | removes the hardware constraint entirely |
| `results.tsv` | `results.tsv` | same logging format, different columns |

---

## Architecture

```
autotrader/
├── backtest.py         # READ-ONLY — historical data download, replay engine, evaluation
├── exchange.py         # READ-ONLY — Kraken live WS, shadow engine (live validation)
├── strategy.py         # AGENT MODIFIES — trading signals, entry/exit logic, sizing
├── program.md          # HUMAN EDITS — agent instructions & constraints
├── data/               # cached historical OHLC data (untracked)
│   └── btceur_1m.csv   # 1-minute BTC/EUR candles from Kraken
├── results.tsv         # experiment log (untracked)
└── pyproject.toml      # dependencies (websockets, numpy, etc.)
```

### `backtest.py` (read-only — primary evaluation harness)

This is the equivalent of `prepare.py`. The agent never touches it. It provides:

1. **Historical data download** — fetches 1-minute BTC/EUR OHLC candles from Kraken's public REST API (`GET /0/public/OHLC`), paginates automatically, caches to `data/btceur_1m.csv`. Incremental updates on re-run.
2. **Replay engine** — iterates through cached candles, builds a tick dict with the exact same interface as the live engine. Synthetic bid/ask from close price +/- estimated spread. Each tick = 1 minute.
3. **Execution simulation** — simulates order fills with realistic slippage (5 bps) and fees (26 bps, Kraken taker default). Same constants as the live shadow engine. No per-cycle trade cap (backtests span weeks/months).
4. **Train/test split** — holds out the last 20% of data as a test set. Reports Sharpe on both. Agent optimizes train Sharpe; test Sharpe monitors overfitting.
5. **Evaluation harness** — computes annualized Sharpe ratio (primary), profit factor, P&L, drawdown, win rate, and other secondary metrics.

Key constants (fixed, not modifiable):

```python
DEFAULT_DAYS = 90             # backtest window
TEST_FRACTION = 0.20          # last 20% held out as test set
MINUTES_PER_YEAR = 525_960    # 365.25 * 24 * 60 (crypto trades 24/7)
SHADOW_START_CASH = 500       # virtual starting cash in EUR
SHADOW_SLIPPAGE_BPS = 5       # simulated slippage: 5 basis points (0.05%)
SHADOW_FEE_BPS = 26           # simulated taker fee: 26 bps (0.26%, Kraken default)
MAX_POSITION_EUR = 100        # hard cap on position size
SPREAD_BPS = 10               # estimated bid-ask spread for synthetic ticks
```

### `exchange.py` (read-only — live validation)

Used for optional live validation of strategies. The agent never touches it. It provides:

1. **Kraken connection** — public WebSocket client (shadow mode) or authenticated REST + WebSocket (live mode)
2. **Market data feed** — real-time BTC/EUR price stream via [Kraken WebSocket v2 API](https://docs.kraken.com/api/)
3. **Shadow execution engine** — simulates order fills against the real-time bid/ask spread. This is "paper trading" since Kraken doesn't offer it for spot crypto.
4. **Live execution** (Phase 3) — place real orders via REST API when ready
5. **Safety rails** — hard-coded limits the agent cannot override (see Safety section)

### `strategy.py` (agent modifies this)

This is the equivalent of `train.py`. The agent's playground. It must expose a class with a specific interface:

```python
class Strategy:
    def __init__(self):
        """Initialize any state, indicators, buffers."""
        ...

    def on_tick(self, tick: dict) -> Signal | None:
        """Called once per candle (1 minute in backtest, 1 second in live).
        
        tick = {
            "timestamp": float,        # unix timestamp
            "price": float,            # close price (backtest) or last trade (live)
            "bid": float,              # best bid (synthetic in backtest)
            "ask": float,              # best ask (synthetic in backtest)
            "spread": float,           # ask - bid
            "volume_24h": float,       # rolling 24h volume
            "history": list[float],    # all previous close prices (grows throughout backtest)
            "elapsed": float,          # seconds since start
            "position": float,         # current position in BTC (signed)
            "pnl": float,              # unrealized P&L in EUR
            "cash": float,             # available EUR balance
            "fills": list[dict],       # list of fills so far
        }

        Returns Signal("buy", size) / Signal("sell", size) / None
        """
        ...

    def on_fill(self, fill: dict):
        """Called when an order is filled. Update internal state."""
        ...
```

The agent can change anything inside this file: the indicators, the signal logic, the position sizing, the entry/exit conditions. It can use moving averages, momentum, mean-reversion, order book imbalance, volatility breakout, whatever it wants. The only constraint is maintaining the interface.

### Example baseline `strategy.py`

```python
"""Baseline: simple momentum crossover (minute-level)."""

from dataclasses import dataclass

@dataclass
class Signal:
    action: str   # "buy" or "sell"
    size: float   # BTC amount

class Strategy:
    def __init__(self):
        self.fast_window = 20    # 20 minutes
        self.slow_window = 60    # 60 minutes (1 hour)
        self.prices = []
        self.position_size = 0.001  # BTC per trade

    def on_tick(self, tick):
        self.prices.append(tick["price"])
        if len(self.prices) < self.slow_window:
            return None
        
        fast_ma = sum(self.prices[-self.fast_window:]) / self.fast_window
        slow_ma = sum(self.prices[-self.slow_window:]) / self.slow_window
        
        if fast_ma > slow_ma and tick["position"] <= 0:
            return Signal("buy", self.position_size)
        elif fast_ma < slow_ma and tick["position"] >= 0:
            return Signal("sell", self.position_size)
        return None

    def on_fill(self, fill):
        pass
```

---

## Evaluation

After each backtest, `backtest.py` computes:

### Primary metric: Annualized Sharpe Ratio

```
sharpe = mean(returns) / std(returns) * sqrt(minutes_per_year)
```

Computed from the per-minute equity curve over the **train** portion of the data (first 80%). The **test** Sharpe (last 20%) is reported separately to monitor overfitting.

Why Sharpe ratio (and why we switched from profit factor):
- **Statistically robust.** With historical backtesting over months of 1-minute data, we get tens of thousands of return observations. Sharpe is the gold standard for evaluating strategies when you have sufficient data.
- **Risk-adjusted.** A high-return, high-volatility strategy scores lower than a steady earner. This naturally penalizes reckless strategies.
- **Industry standard.** Sharpe > 1.0 is good, > 2.0 is strong, > 3.0 is exceptional. Directly comparable across strategies and timeframes.
- **Why not profit factor anymore?** Profit factor was chosen when we were evaluating on live 10-minute windows with 5-30 trades — too few data points for Sharpe to be meaningful. With historical backtesting producing hundreds or thousands of trades, that constraint is gone. Sharpe is strictly better with sufficient data.

Annualization: crypto trades 24/7/365.25, so `minutes_per_year = 525,960`.

### Secondary metrics (logged but not used for keep/discard)

| metric | description |
|---|---|
| `sharpe_test` | Sharpe ratio on the held-out test set (last 20% of data) |
| `profit_factor` | gross profit / gross loss (capped at 10.0, 0.0 if < 3 trades) |
| `total_pnl` | net profit/loss in EUR |
| `num_trades` | total trades executed |
| `max_drawdown` | worst peak-to-trough drop in EUR |
| `win_rate` | fraction of profitable trades |
| `avg_trade_pnl` | average P&L per trade |
| `time_in_market` | fraction of time with an open position |

### Output format (printed after each backtest)

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

### `results.tsv`

```
commit	sharpe_ratio	sharpe_test	pnl_eur	num_trades	status	description
a1b2c3d	0.00	0.00	0.00	0	keep	baseline (momentum crossover)
b2c3d4e	1.85	1.42	47.30	312	keep	add RSI filter with 60-min window
c3d4e5f	2.10	0.30	52.10	890	discard	aggressive scalping (overfit)
d4e5f6g	0.00	0.00	0.00	0	crash	division by zero in volatility calc
```

---

## Kraken API Usage

Based on [Kraken API docs](https://docs.kraken.com/api/):

### What we need

| capability | API | endpoint |
|---|---|---|
| Real-time prices | **WebSocket v2** | `subscribe` to `ticker` and `trade` channels for `BTC/EUR` |
| Place orders | **Spot REST** | `POST /0/private/AddOrder` |
| Cancel orders | **Spot REST** | `POST /0/private/CancelOrder` |
| Check balances | **Spot REST** | `POST /0/private/Balance` |
| Open positions | **Spot REST** | `POST /0/private/OpenPositions` |
| Trade history | **Spot REST** | `POST /0/private/TradesHistory` |
| OHLCV candles | **Spot REST** | `GET /0/public/OHLC` (for historical context) |
| Order book | **Spot REST** | `GET /0/public/Depth` |

### Backtesting (Phase 1 & 2): Public REST API only

`backtest.py` uses the **public** OHLC REST endpoint to download historical 1-minute candles. No API key, no authentication. Data is cached locally in `data/btceur_1m.csv` and incrementally updated on re-run. The backtest execution engine simulates fills against historical close prices with synthetic bid/ask spread.

### Live validation (Phase 2): Public WebSocket only

`exchange.py` uses the **public** WebSocket to receive real-time bid/ask/price updates for live shadow validation. No API key needed. The shadow execution engine simulates fills locally.

### Live mode (Phase 3): Authenticated API

When going live, we add the Kraken REST API for real order placement. Use **[ccxt](https://github.com/ccxt/ccxt)** (or **[krakenex](https://github.com/veox/python3-krakenex)**) to abstract the REST calls and handle auth/nonces.

API keys go in environment variables (never in code):

```bash
export KRAKEN_API_KEY="your-api-key"
export KRAKEN_API_SECRET="your-api-secret"
```

`exchange.py` reads these at startup. The agent never sees or modifies credentials. In shadow mode, these env vars are not required.

---

## Safety: This Involves Real Money (Eventually)

This is the single biggest difference from autoresearch. A buggy `train.py` wastes GPU time. A buggy `strategy.py` can lose real money once we go live. Safety must be **defense in depth** — multiple independent layers, all in the read-only `exchange.py`.

### Layer 1: Shadow mode (default — weeks/months)

The system starts in **shadow mode**. It connects to Kraken's **public** WebSocket API (no API key needed) to receive real-time BTC/EUR prices. All order execution is simulated internally — `exchange.py` maintains a virtual portfolio and fills orders against the real bid/ask spread with simulated slippage and fees. No real orders. No real money at risk.

Kraken doesn't offer paper trading for spot crypto (only for [futures via demo-futures.kraken.com](https://support.kraken.com/hc/en-us/articles/360026192132-Futures-Demo-Test-accounts)). So we build our own shadow execution engine.

The shadow engine models realistic conditions:
- **Slippage:** configurable (default 5 bps / 0.05%)
- **Fees:** simulated at Kraken taker rate (26 bps / 0.26%). When going live, these can be reduced or removed depending on your Kraken fee tier.
- **Fill logic:** market orders fill at bid (sells) or ask (buys) + slippage. No partial fills in shadow mode.

### Layer 2: Hard limits in `exchange.py` (not overridable by agent)

```python
MAX_POSITION_EUR = 100       # never hold more than €100 of BTC
MAX_LOSS_PER_CYCLE_EUR = 10  # auto-liquidate if cycle loss exceeds €10
MAX_TRADES_PER_CYCLE = 50    # prevent order spam
MIN_ORDER_SIZE_BTC = 0.0001  # Kraken minimum
```

If any limit is hit, `exchange.py` force-closes the position and ends the cycle early. The experiment is logged with a `safety_stop` status.

These limits apply in both shadow and live modes — the shadow engine enforces them identically so the agent learns to stay within them before real money is involved.

### Layer 3: Kraken-side controls (live mode only)

- Use a **restricted API key** with:
  - Trade permission only (no withdrawal)
  - BTC/EUR pair only
- Fund the account with only what you're willing to lose

### Layer 4: Cycle isolation

Each 10-minute cycle starts and ends flat (no open positions). If the strategy is discarded, any remaining position is closed before the next experiment. No state leaks between experiments.

### Layer 5: Kill switch

A simple mechanism (e.g., a `STOP` file in the repo root, or a keyboard interrupt handler) that:
1. Immediately cancels all open orders (live mode)
2. Closes all positions
3. Logs the state
4. Exits cleanly

---

## The Experiment Loop

Adapted from `program.md`:

```
LOOP FOREVER:

1. Look at git state: current branch/commit
2. Modify strategy.py with an experimental idea
3. git commit
4. Run the backtest: uv run backtest.py > run.log 2>&1
5. Read out results: grep "^sharpe_ratio:\|^sharpe_test:" run.log
6. If grep is empty → crash. Read tail of run.log, attempt fix.
7. Log to results.tsv
8. If sharpe_ratio improved → keep (advance branch)
9. If sharpe_ratio is equal or worse → discard (git reset)
10. Watch for overfitting: if train >> test, discard even if train improved
```

### Key differences from autoresearch loop

- **Deterministic feedback.** Like autoresearch's fixed validation set, the backtest uses the same historical data every time. Same strategy = same result. The agent can make incremental changes and reliably tell whether they helped. This was the critical improvement over the original live-trading approach (see Resolved Decisions).
- **Fast iteration.** Each backtest completes in seconds (not 10 minutes). The agent can run hundreds of experiments per day.
- **Overfitting is the main risk.** With deterministic backtesting against fixed historical data, the agent can easily overfit to historical noise. Mitigations:
  - The 80/20 train/test split catches strategies that memorize patterns. If train Sharpe is high but test Sharpe is low, the strategy is overfitting — discard it.
  - Prefer strategies with a clear theoretical basis (momentum, mean-reversion, volatility breakout) over heavily tuned magic numbers.
  - Simpler strategies with fewer parameters tend to generalize better.
  - Periodically validate against live market data using `exchange.py` as a sanity check.
- **Crashes are harmless.** A crash in backtesting has no consequences — just fix and re-run.

---

## What the Agent Can Experiment With

The strategy design space is huge. Some directions:

### Signal generation
- Moving average crossovers (SMA, EMA, WMA)
- Momentum / rate of change
- Mean reversion (Bollinger bands, z-score)
- RSI, MACD, Stochastic oscillator
- Order book imbalance (bid/ask volume ratio)
- Volatility breakout (ATR-based)
- Price action patterns (support/resistance, breakouts)
- Microstructure signals (spread changes, trade flow)

### Position sizing
- Fixed size
- Volatility-scaled (Kelly criterion, ATR-based)
- Confidence-weighted

### Risk management
- Stop-loss / take-profit levels
- Trailing stops
- Time-based exits
- Maximum drawdown circuit breaker

### Regime detection
- Trend vs. range-bound detection
- Volatility regime switching
- Multi-timeframe analysis

### Combinations
- Ensemble of signals with voting
- Signal filtering (only trade when multiple indicators agree)
- Adaptive parameters (adjust windows based on recent volatility)

---

## Resolved Decisions

1. **Historical backtesting as primary loop (not live forward-testing).** The original design ran each strategy against a live 10-minute Kraken WebSocket window. This produced 5-30 trades per experiment — statistically meaningless noise. Two identical strategies run 10 minutes apart could score differently due to market conditions. We switched to historical backtesting against months of 1-minute OHLC data: deterministic (same strategy = same result), fast (seconds not minutes), and statistically robust (hundreds/thousands of trades). The live shadow engine (`exchange.py`) is retained for optional validation. See Evaluation section for details.

2. **Primary metric: Annualized Sharpe Ratio.** Risk-adjusted return computed from the per-minute equity curve. Sharpe > 1.0 is good, > 2.0 is strong. We originally chose profit factor because it was stable with few trades (a 10-minute window only produces 5-30). With historical backtesting providing thousands of trades, Sharpe is strictly better — it's the industry standard for strategy evaluation. Profit factor is still computed as a secondary metric.

3. **Shadow mode first, live later.** Kraken has no spot paper trading ([only futures demo](https://support.kraken.com/hc/en-us/articles/360026192132-Futures-Demo-Test-accounts)). We build our own execution simulation that models realistic slippage and fees. The agent iterates in backtest mode first. Live validation via `exchange.py` uses Kraken's public WebSocket. Live mode is Phase 3.

4. **Fees: simulated now, potentially zero later.** Both `backtest.py` and `exchange.py` simulate Kraken's default taker fee (0.26%) so the agent learns to account for transaction costs. If a strategy's edge is eaten by fees, that's a legitimate finding — the agent should discover fee-aware strategies. Note: [Kraken+](https://kraken.com/kraken-plus) (~€4.99/month) offers zero-fee trading up to ~€10K/month but **only on the Kraken web/app, NOT through API trades**. API trades on Kraken Pro still use the [maker/taker fee schedule](https://www.kraken.com/en-us/features/fee-schedule/#kraken-pro) (0.16%/0.26% at low volume, decreasing with higher 30-day volume). So when going live, we configure the fee constant to match your actual Kraken fee tier. Higher volume = lower fees = more strategies become viable.

5. **Data context: generous.** The `tick["history"]` buffer holds all previous close prices throughout the backtest (growing list, can reach 100K+ entries over 90 days). The strategy slices what it needs. No reason to be stingy — it's just a list of floats in memory.

6. **Quote currency: EUR.** The Kraken account uses EUR, so we trade BTC/EUR. Kraken's BTC/EUR pair has good liquidity (one of the most traded EUR pairs on Kraken) but lower volume than BTC/USD, which may mean slightly wider spreads during low-activity hours. The backtest spread estimate (10 bps) and slippage constant (5 bps) account for this. Fee schedule is identical regardless of quote currency. All P&L, position limits, and cash amounts are denominated in EUR.

7. **Tick granularity: 1 minute.** Kraken's public OHLC API provides 1-minute candles — the finest resolution available. Each tick in the backtest represents one 1-minute candle (close price, with synthetic bid/ask). This means strategy parameters should be calibrated in minutes: a 120-tick lookback covers 2 hours. For live validation via `exchange.py`, ticks are 1 second — the strategy should be designed to work at both timescales, or the agent should adapt parameters when switching.

8. **Train/test split: 80/20.** The backtest automatically holds out the last 20% of data as a test set. The agent optimizes train Sharpe; test Sharpe monitors overfitting. With 90 days default, that's 72 days train / 18 days test.

## Remaining Open Questions

1. **Backtest vs. live divergence.** The backtest simulates execution with estimated spread and slippage, but can't model market impact, partial fills, or order book dynamics. Live validation via `exchange.py` helps catch divergence. When going live, run shadow in parallel as a sanity check.

2. **Data staleness.** Historical data is a snapshot. Market microstructure evolves — strategies optimized on old data may not work on future data. Periodically re-download and re-evaluate. Walk-forward validation (re-train on rolling windows) is a future improvement.

3. **Time-of-day effects.** Crypto trades 24/7 but volatility varies by time of day. The agent might discover time-of-day patterns in the backtest. Whether these generalize to future data is an open question.

4. **Legal/compliance.** Automated trading with Kraken is allowed per their terms but worth reviewing for your jurisdiction. No special license needed for personal use.

---

## Implementation Plan

### Phase 1: Infrastructure scaffold
- [x] Write `exchange.py` with Kraken public WebSocket price feed + shadow execution engine
- [x] Write `backtest.py` with historical data download, replay engine, Sharpe evaluation
- [x] Define the `Strategy` interface and write baseline `strategy.py`
- [x] Implement evaluation harness (Sharpe ratio, profit factor, P&L, drawdown, etc.)
- [x] Adapt `program.md` for backtest-first workflow
- [x] No API key needed — backtesting uses only Kraken's public endpoints

### Phase 2: Agent loop (backtest-first)
- [ ] Let the agent loose on `strategy.py` with `backtest.py` as primary evaluator
- [ ] Run overnight, review results in the morning
- [ ] Iterate on `program.md` based on what the agent tries
- [ ] Periodically validate best strategies with live shadow mode (`exchange.py`)
- [ ] Run for days/weeks until strategies show robust train + test Sharpe

### Phase 3: Live trading (cautious)
- [ ] Create restricted Kraken API key (trade only, no withdrawal, BTC/EUR only)
- [ ] Fund account with money you're willing to lose
- [ ] Switch `MODE = "live"` in `exchange.py`
- [ ] Run shadow in parallel to detect divergence
- [ ] Monitor closely for the first cycles
- [ ] Adjust `SHADOW_FEE_BPS` to match your actual Kraken fee tier

---

## Dependencies

```toml
[project]
dependencies = [
    "websockets>=12.0",    # Kraken public WebSocket feed
    "numpy>=1.26",         # math (profit factor, drawdown, etc.)
    # Phase 3 (live mode) adds:
    # "ccxt>=4.0",         # Kraken REST API for real order execution
]
```

No PyTorch. No GPU. No API key (in shadow mode). Runs on any machine with Python 3.10+ and internet.
