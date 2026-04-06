# Phase 3: Live Trading

Phase 3 is the cautious handoff from research to real execution. In `spec.md`, this phase is explicitly gated behind strong backtest results and shadow validation.

## Planned Live Architecture

```mermaid
flowchart TD
    Strategy[strategy.py]
    Exchange[exchange.py live mode]
    WS[Kraken WebSocket<br/>market data]
    REST[Kraken REST API<br/>orders + balances]
    Reconcile[Balance sync and reconciliation]
    Safety[Hard safety rails]
    Shadow[Run shadow in parallel]
    Human[Human monitoring]

    WS --> Exchange
    Strategy --> Exchange
    Exchange --> REST
    REST --> Reconcile
    Reconcile --> Exchange
    Exchange --> Safety
    Safety --> Shadow
    Shadow --> Human
    Safety --> Human
```



## Intended Cycle

```mermaid
sequenceDiagram
    participant M as Kraken market data
    participant E as exchange.py
    participant S as strategy.py
    participant K as Kraken REST

    M->>E: live bid/ask/price updates
    E->>K: fetch current balances
    K-->>E: EUR and BTC balances
    E->>S: on_tick(tick with true balances)
    S-->>E: buy/sell signal or None
    E->>K: place order if allowed
    K-->>E: fill and updated balances
    E->>S: on_fill(fill)
    E->>E: reconcile internal state with Kraken
```



## Safety Layers

```mermaid
flowchart LR
    Strategy[Strategy signal] --> Limits[Position and trade limits]
    Limits --> Loss[Per-cycle loss stop]
    Loss --> Reconcile[Balance reconciliation]
    Reconcile --> Kill[STOP file / kill switch]
    Kill --> Orders[Cancel orders and close exposure]
```



## Live-Phase Responsibilities

- Use a restricted Kraken API key with no withdrawal permission.
- Keep the live portfolio aligned with the backtest starting assumptions as closely as possible.
- Query balances after fills so the strategy sees the true account state.
- Reconcile on every decision cycle to catch drift from rounding, fees, partial fills, or manual intervention.
- Keep shadow validation running in parallel as a sanity check.

## Current Status

This phase is mostly planned rather than implemented. The repository already has the shadow runtime and safety mindset, but real order placement, balance synchronization, and full reconciliation remain future work called out in `spec.md`.