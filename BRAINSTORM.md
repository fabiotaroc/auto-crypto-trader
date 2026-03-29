# autotrader: autonomous trading strategy research

## The Idea

Adapt the [autoresearch](https://github.com/karpathy/autoresearch) feedback loop for algorithmic trading. Instead of an AI agent iterating on a neural network training script to minimize `val_bpb`, the agent iterates on a **trading strategy script** to maximize **profit factor** (a risk-adjusted profitability metric). The agent connects to [Kraken](https://docs.kraken.com/api/) to trade BTC/USD, runs each strategy variant for a fixed 10-minute window, evaluates the result, keeps or discards, and repeats.

The first weeks/months run in **shadow mode** — reading real market data from Kraken's public API but only simulating trades internally. No real orders, no real money. Once the agent has iterated enough to find consistently profitable strategies, we flip the switch to live.

You wake up in the morning to a log of strategy experiments and (hopefully) a profitable algorithm.

---

## Mapping autoresearch → autotrader

| autoresearch | autotrader | notes |
|---|---|---|
| `train.py` — model, optimizer, training loop | `strategy.py` — trading logic, signals, position sizing | **the one file the agent edits** |
| `prepare.py` — data prep, eval, constants | `exchange.py` — Kraken API connection, market data, evaluation harness | **read-only, not modified by agent** |
| `program.md` — agent instructions | `program.md` — agent instructions (adapted for trading) | **edited by human** |
| `val_bpb` (lower is better) | profit factor (higher is better), plus secondary metrics | the ground-truth fitness metric |
| 5-min fixed training budget | 10-min fixed trading window | wall-clock time per experiment |
| single NVIDIA GPU | no GPU needed — just a Kraken API key and internet | removes the hardware constraint entirely |
| `results.tsv` | `results.tsv` | same logging format, different columns |

---

## Architecture

```
autotrader/
├── exchange.py         # READ-ONLY — Kraken public WS, shadow engine, evaluation, constants
├── strategy.py         # AGENT MODIFIES — trading signals, entry/exit logic, sizing
├── program.md          # HUMAN EDITS — agent instructions & constraints
├── results.tsv         # experiment log (untracked)
├── shadow_ledger.json  # tracks simulated fills in shadow mode (untracked)
└── pyproject.toml      # dependencies (websockets, numpy, etc.)
```

### `exchange.py` (read-only infrastructure)

This is the equivalent of `prepare.py`. The agent never touches it. It provides:

1. **Kraken connection** — public WebSocket client (shadow mode) or authenticated REST + WebSocket (live mode)
2. **Market data feed** — real-time BTC/USD price stream via [Kraken WebSocket v2 API](https://docs.kraken.com/api/)
3. **Shadow execution engine** — simulates order fills against the real-time bid/ask spread, tracks a virtual portfolio internally. No real orders placed. This is our "paper trading" since Kraken doesn't offer paper trading for spot crypto.
4. **Live execution** (Phase 3) — place real orders via REST API when we're ready
5. **Account state** — virtual balances, positions, P&L (shadow mode) or real account state (live mode)
6. **Evaluation harness** — computes profit factor and secondary metrics after each 10-min window
7. **Safety rails** — hard-coded limits the agent cannot override (see Safety section)

Key constants (fixed, not modifiable):

```python
TRADING_PAIR = "BTC/USD"
CYCLE_DURATION = 600          # 10-minute trading window (seconds)
MAX_POSITION_USD = 100        # hard cap on position size
MAX_LOSS_PER_CYCLE_USD = 10   # stop-loss per experiment
MAX_TRADES_PER_CYCLE = 50     # prevent runaway order spam
TICK_INTERVAL = 1             # strategy called every 1 second
MODE = "shadow"               # "shadow" or "live" — default shadow
SHADOW_START_CASH = 500       # virtual starting cash for shadow mode
SHADOW_SLIPPAGE_BPS = 5       # simulated slippage: 5 basis points (0.05%)
SHADOW_FEE_BPS = 26           # simulated taker fee: 26 bps (0.26%, Kraken default)
```

### `strategy.py` (agent modifies this)

This is the equivalent of `train.py`. The agent's playground. It must expose a class with a specific interface:

```python
class Strategy:
    def __init__(self):
        """Initialize any state, indicators, buffers."""
        ...

    def on_tick(self, tick: dict) -> Signal | None:
        """Called every TICK_INTERVAL seconds with latest market data.
        
        tick = {
            "timestamp": float,        # unix timestamp
            "price": float,            # last trade price
            "bid": float,              # best bid
            "ask": float,              # best ask
            "spread": float,           # ask - bid
            "volume_24h": float,       # 24h volume
            "history": list[float],    # full price history for this cycle (grows up to 600 ticks)
            "elapsed": float,          # seconds since cycle start
            "position": float,         # current position in BTC (signed)
            "pnl": float,              # unrealized P&L in USD
            "cash": float,             # available USD balance
            "fills": list[dict],       # list of fills so far this cycle
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
"""Baseline: simple momentum crossover."""

from dataclasses import dataclass

@dataclass
class Signal:
    action: str   # "buy" or "sell"
    size: float   # BTC amount

class Strategy:
    def __init__(self):
        self.fast_window = 20   # ~20 seconds
        self.slow_window = 60   # ~60 seconds
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

After each 10-minute window, `exchange.py` computes:

### Primary metric: Profit Factor

```
profit_factor = gross_profit / gross_loss
```

Why profit factor over Sharpe ratio:
- **Stable with few trades.** Sharpe needs many data points to be meaningful. In a 10-minute window we might have 5–30 trades. Profit factor works with any number of closed trades.
- **Intuitive.** A profit factor of 2.0 means for every $1 lost, $2 was gained. Above 1.0 = profitable, below 1.0 = losing. Industry standard considers 1.75+ as strong for short-term strategies.
- **Accounts for risk implicitly.** Unlike raw P&L, it penalizes strategies that take big losses even if they occasionally win big.

Edge cases:
- **Zero trades:** profit factor = 0.0 (neutral baseline). The agent should learn that doing nothing scores zero.
- **No losses (all trades profitable):** cap at 10.0 to avoid infinity. A strategy with only winners over 10 minutes was probably lucky — we don't want to overreward it.
- **No wins (all trades losers):** the raw value (0.0). Strategy clearly failed.
- **Fewer than 3 trades:** profit factor = 0.0. Too few trades to evaluate meaningfully — treat as baseline.

### Secondary metrics (logged but not used for keep/discard)

| metric | description |
|---|---|
| `total_pnl` | net profit/loss in USD |
| `num_trades` | total trades executed |
| `max_drawdown` | worst peak-to-trough drop in USD |
| `win_rate` | fraction of profitable trades |
| `avg_trade_pnl` | average P&L per trade |
| `sharpe_ratio` | risk-adjusted return (logged for reference) |
| `time_in_market` | fraction of time with an open position |

### Output format (printed after each cycle)

```
---
profit_factor:    2.150000
total_pnl_usd:    3.42
num_trades:       11
max_drawdown_usd: 1.05
win_rate:         0.636
sharpe_ratio:     1.23
time_in_market:   0.52
cycle_seconds:    600.0
mode:             shadow
```

### `results.tsv`

```
commit	profit_factor	pnl_usd	num_trades	status	description
a1b2c3d	0.000000	0.00	0	keep	baseline (momentum crossover)
b2c3d4e	1.850000	1.23	12	keep	add RSI filter
c3d4e5f	0.620000	-2.10	45	discard	aggressive scalping (overtrading)
d4e5f6g	0.000000	0.00	0	crash	division by zero in volatility calc
```

---

## Kraken API Usage

Based on [Kraken API docs](https://docs.kraken.com/api/):

### What we need

| capability | API | endpoint |
|---|---|---|
| Real-time prices | **WebSocket v2** | `subscribe` to `ticker` and `trade` channels for `BTC/USD` |
| Place orders | **Spot REST** | `POST /0/private/AddOrder` |
| Cancel orders | **Spot REST** | `POST /0/private/CancelOrder` |
| Check balances | **Spot REST** | `POST /0/private/Balance` |
| Open positions | **Spot REST** | `POST /0/private/OpenPositions` |
| Trade history | **Spot REST** | `POST /0/private/TradesHistory` |
| OHLCV candles | **Spot REST** | `GET /0/public/OHLC` (for historical context) |
| Order book | **Spot REST** | `GET /0/public/Depth` |

### Shadow mode (Phase 1 & 2): Public API only

Shadow mode only needs the **public** WebSocket — no API key, no authentication. We subscribe to the `ticker` channel for BTC/USD and get real-time bid/ask/price updates. The shadow execution engine simulates fills locally.

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

The system starts in **shadow mode**. It connects to Kraken's **public** WebSocket API (no API key needed) to receive real-time BTC/USD prices. All order execution is simulated internally — `exchange.py` maintains a virtual portfolio and fills orders against the real bid/ask spread with simulated slippage and fees. No real orders. No real money at risk.

Kraken doesn't offer paper trading for spot crypto (only for [futures via demo-futures.kraken.com](https://support.kraken.com/hc/en-us/articles/360026192132-Futures-Demo-Test-accounts)). So we build our own shadow execution engine.

The shadow engine models realistic conditions:
- **Slippage:** configurable (default 5 bps / 0.05%)
- **Fees:** simulated at Kraken taker rate (26 bps / 0.26%). When going live, these can be reduced or removed depending on your Kraken fee tier.
- **Fill logic:** market orders fill at bid (sells) or ask (buys) + slippage. No partial fills in shadow mode.

### Layer 2: Hard limits in `exchange.py` (not overridable by agent)

```python
MAX_POSITION_USD = 100       # never hold more than $100 of BTC
MAX_LOSS_PER_CYCLE_USD = 10  # auto-liquidate if cycle loss exceeds $10
MAX_TRADES_PER_CYCLE = 50    # prevent order spam
MIN_ORDER_SIZE_BTC = 0.0001  # Kraken minimum
```

If any limit is hit, `exchange.py` force-closes the position and ends the cycle early. The experiment is logged with a `safety_stop` status.

These limits apply in both shadow and live modes — the shadow engine enforces them identically so the agent learns to stay within them before real money is involved.

### Layer 3: Kraken-side controls (live mode only)

- Use a **restricted API key** with:
  - Trade permission only (no withdrawal)
  - BTC/USD pair only
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
4. Run the experiment: uv run exchange.py > run.log 2>&1
5. Read out results: grep "^profit_factor:\|^total_pnl_usd:" run.log
6. If grep is empty → crash. Read tail of run.log, attempt fix.
7. Log to results.tsv
8. If profit_factor improved → keep (advance branch)
9. If profit_factor is equal or worse → discard (git reset)
```

### Key differences from autoresearch loop

- **Must close positions** between experiments. The agent waits for `exchange.py` to confirm flat before starting the next cycle.
- **Market conditions change.** Unlike autoresearch where `val_bpb` is evaluated on a fixed dataset (deterministic), here the market is the "validation set" and it's non-stationary. Two identical strategies run 10 minutes apart may get different results due to changing volatility, trend, or news. This is inherent noise. Mitigations:
  - Accept the noise and rely on the trend over many experiments. Over dozens/hundreds of cycles, consistently good strategies will float to the top and lucky flukes will regress.
  - The agent can reason about this — if a strategy idea has a strong theoretical basis and shows marginal improvement, it might keep it even with a noisy result.
  - This is actually realistic: real daytrading strategies must be robust to market regime changes. A strategy that only works in specific conditions is fragile and the agent should learn to discard it.
- **Crashes are less dangerous in shadow mode.** In shadow mode, a crash just loses the virtual state — no real consequences. In live mode, a crash mid-trade could leave an open position. `exchange.py` must have a robust `try/finally` block that closes positions on any exit.

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

1. **Cycle duration: 10 minutes.** Longer than autoresearch's 5 minutes. BTC/USD needs enough time for the strategy to take positions and see them play out. 10 minutes gives ~6 experiments/hour, ~50 overnight. This is daytrading, so we want volatility — 10 minutes is a sweet spot between enough data and fast iteration.

2. **Primary metric: Profit Factor.** Gross profit / gross loss. Stable with few trades, intuitive (>1.0 = profitable), industry standard for short-term strategies. See Evaluation section for full rationale and edge cases.

3. **Shadow mode first, live later.** Kraken has no spot paper trading ([only futures demo](https://support.kraken.com/hc/en-us/articles/360026192132-Futures-Demo-Test-accounts)). We build our own shadow execution engine that reads real market data from Kraken's public WebSocket and simulates fills internally with realistic slippage and fees. The agent iterates for days/weeks in shadow mode. Live mode is Phase 3.

4. **Fees: simulated now, potentially zero later.** Shadow mode simulates Kraken's default taker fee (0.26%) so the agent learns to account for transaction costs. If a strategy's edge is eaten by fees, that's a legitimate finding — the agent should discover fee-aware strategies. Note: [Kraken+](https://kraken.com/kraken-plus) ($4.99/month) offers zero-fee trading up to $10K/month but **only on the Kraken web/app, NOT through API trades**. API trades on Kraken Pro still use the [maker/taker fee schedule](https://www.kraken.com/en-us/features/fee-schedule/#kraken-pro) (0.16%/0.26% at low volume, decreasing with higher 30-day volume). So when going live, we configure the fee constant to match your actual Kraken fee tier. Higher volume = lower fees = more strategies become viable.

5. **Data context: generous.** The `tick["history"]` buffer holds the full 10-minute price history (600 ticks at 1/sec). The strategy can also request OHLCV candles from Kraken's public REST API for longer historical context. No reason to be stingy — it's just a list of floats in memory.

## Remaining Open Questions

1. **Market hours.** Crypto trades 24/7, which is a plus — the agent can run indefinitely. But volatility varies by time of day. The agent might learn time-of-day patterns.

2. **Shadow vs. live divergence.** The shadow engine simulates slippage and fees, but can't model market impact, partial fills, or order book dynamics perfectly. When we go live, we should run shadow in parallel as a sanity check to flag divergence.

3. **Legal/compliance.** Automated trading with Kraken is allowed per their terms but worth reviewing for your jurisdiction. No special license needed for personal use.

---

## Implementation Plan

### Phase 1: Shadow mode scaffold
- [ ] Write `exchange.py` with Kraken public WebSocket price feed + shadow execution engine
- [ ] Define the `Strategy` interface and write baseline `strategy.py`
- [ ] Implement evaluation harness (profit factor, P&L, drawdown, etc.)
- [ ] Adapt `program.md` for trading context
- [ ] Test manually: run one 10-min cycle and verify metrics
- [ ] No API key needed — shadow mode uses only Kraken's public endpoints

### Phase 2: Agent loop (shadow)
- [ ] Let the agent loose on `strategy.py` in shadow mode
- [ ] Run overnight, review results in the morning
- [ ] Iterate on `program.md` based on what the agent tries
- [ ] Run for days/weeks until strategies are consistently profitable

### Phase 3: Live trading (cautious)
- [ ] Create restricted Kraken API key (trade only, no withdrawal, BTC/USD only)
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
