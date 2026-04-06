# System Architecture

This folder explains how `autotrader` works end to end, organized by the same phases defined in `spec.md`.

If you want the project rationale and roadmap source of truth, start with `spec.md`. If you want the operational view of the system, start here.

## Phase Map

- `phase-1-infrastructure.md` covers the fixed runtime scaffold: data ingestion, replay, shadow execution, metrics, and the strategy contract.
- `phase-2-agent-loop.md` covers the autonomous research loop that edits `strategy.py`, runs backtests, and decides whether to keep or discard experiments.
- `phase-3-live-trading.md` covers the planned live-trading architecture, including reconciliation, safety rails, and the shadow/live pairing.

## System Overview

```mermaid
flowchart LR
    Human[Human operator]
    Brainstorm[spec.md<br/>design + roadmap]
    Program[program.md<br/>agent instructions]
    Agent[LLM agent]
    Strategy[strategy.py<br/>editable strategy]
    Backtest[backtest.py<br/>historical evaluator]
    Exchange[exchange.py<br/>shadow/live runtime]
    KrakenREST[Kraken REST API]
    KrakenWS[Kraken WebSocket API]
    Data[(data/btceur_1m.csv)]
    Results[(results.tsv)]

    Human --> Brainstorm
    Human --> Program
    Brainstorm --> Agent
    Program --> Agent
    Agent --> Strategy
    Strategy --> Backtest
    Strategy --> Exchange
    KrakenREST --> Backtest
    Backtest <--> Data
    Backtest --> Results
    KrakenWS --> Exchange
```



## Runtime Shape

```mermaid
flowchart TD
    Start[Choose mode] --> P1[Phase 1<br/>build the scaffold]
    P1 --> P2[Phase 2<br/>agent iterates on strategy]
    P2 --> P2a[Primary path:<br/>historical backtest]
    P2 --> P2b[Optional check:<br/>live shadow validation]
    P2a --> Promote{Robust train + test Sharpe?}
    P2b --> Promote
    Promote -- no --> P2
    Promote -- yes --> P3[Phase 3<br/>restricted live trading]
```



## Core Design Rule

The system is intentionally split into two layers:

- Fixed infrastructure: `backtest.py` and `exchange.py` own data ingestion, execution simulation, safety limits, and metrics.
- Search surface: `strategy.py` is the single file the agent is supposed to change.

That separation is what makes the loop auditable, repeatable, and safe enough to automate.