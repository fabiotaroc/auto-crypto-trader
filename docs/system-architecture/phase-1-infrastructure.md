# Phase 1: Infrastructure Scaffold

Phase 1 builds the fixed machinery that every experiment depends on. The key idea is that the strategy is pluggable, but the market data, execution model, and evaluation logic are not.

## What Exists In This Phase

- `backtest.py`: downloads and caches Kraken BTC/EUR data, replays it, simulates fills, and computes metrics.
- `exchange.py`: consumes Kraken live ticker data and runs a shadow trading cycle against the same strategy interface.
- `strategy.py`: exposes `Strategy.on_tick()` and `Strategy.on_fill()`.
- `program.md`: tells the agent how to run experiments without changing the infrastructure.

## Shared Strategy Contract

Both runtimes import the same `Strategy` class and pass it a `tick` dict containing current market state, account state, and accumulated history.

```mermaid
flowchart LR
    Backtest[backtest.py]
    Exchange[exchange.py]
    Tick[tick payload<br/>price, bid, ask, spread,<br/>history, cash, position, fills]
    Strategy[strategy.py]
    Signal[Signal buy/sell]
    Fill[fill event]

    Backtest --> Tick
    Exchange --> Tick
    Tick --> Strategy
    Strategy --> Signal
    Signal --> Backtest
    Signal --> Exchange
    Backtest --> Fill
    Exchange --> Fill
    Fill --> Strategy
```



## Historical Backtest Path

This is the main evaluation engine for the whole project.

```mermaid
flowchart TD
    Kraken[Kraken public REST]
    Cache[(data/btceur_1m.csv)]
    Load[Load candles for N days]
    Replay[Replay 1-minute candles]
    Decide[Every 15 candles<br/>build tick + call strategy]
    Execute[Simulate buy/sell<br/>with spread, slippage, fees]
    Equity[Update equity curve]
    Split[Split equity into 80 percent train<br/>and 20 percent test]
    Metrics[Compute Sharpe, PnL,<br/>drawdown, win rate, trade count]

    Kraken --> Cache
    Cache --> Load
    Load --> Replay
    Replay --> Decide
    Decide --> Execute
    Execute --> Equity
    Equity --> Split
    Split --> Metrics
```



## Live Shadow Path

`exchange.py` is the reality check. It uses live Kraken prices but still executes locally in shadow mode.

```mermaid
flowchart TD
    WS[Kraken WebSocket v2 ticker]
    Market[Latest market state<br/>price, bid, ask, volume]
    Loop[Cycle loop]
    Tick[Build live tick]
    Strategy[strategy.py]
    Shadow[ShadowEngine execute]
    Safety[Safety checks<br/>max loss, max trades, STOP file]
    Metrics[End-of-cycle metrics]

    WS --> Market
    Market --> Loop
    Loop --> Tick
    Tick --> Strategy
    Strategy --> Shadow
    Shadow --> Safety
    Safety --> Loop
    Safety --> Metrics
```



## Fixed Constraints

- Historical data uses Kraken public endpoints and is cached locally.
- Backtests start from `500 EUR + 0.001 BTC`.
- Execution includes synthetic spread, slippage, and Kraken-style taker fees.
- Position size is capped by infrastructure, not by the strategy.
- Metrics are computed by the infrastructure so the strategy cannot game the evaluator.

## Important Implementation Note

The project plan describes a unified 15-minute decision cadence across modes. The current code already does that in `backtest.py`, but `exchange.py` currently calls the strategy on a 1-second tick during a shadow cycle. The docs here reflect the code as it exists today, not just the intended roadmap.