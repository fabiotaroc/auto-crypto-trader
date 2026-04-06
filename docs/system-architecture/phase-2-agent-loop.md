# Phase 2: Agent Loop

Phase 2 turns the scaffold into an autonomous research system. The agent is not changing the engine; it is repeatedly searching the strategy space by editing only `strategy.py`.

## Control Loop

```mermaid
flowchart TD
    Start[Start run on autotrader branch]
    Refresh[Refresh historical data<br/>uv run backtest.py --update-data]
    Baseline[Run baseline strategy]
    Edit[Edit strategy.py]
    Commit[Commit experiment]
    Run[Run uv run backtest.py]
    Parse[Read Sharpe, test Sharpe, trades]
    Decide{Keep or discard?}
    Log[Append results.tsv]
    Revert[Reset experiment]
    Keep[Advance branch]
    Shadow[Optional live shadow validation]

    Start --> Refresh
    Refresh --> Baseline
    Baseline --> Edit
    Edit --> Commit
    Commit --> Run
    Run --> Parse
    Parse --> Decide
    Decide --> Log
    Log --> Revert
    Log --> Keep
    Keep --> Shadow
    Revert --> Edit
    Shadow --> Edit
```



## Why This Loop Works

- The backtest is deterministic, so the same strategy gets the same result.
- Each run completes quickly, so the agent can explore many ideas.
- The strategy surface is narrow enough to audit because changes stay in one file.
- The train/test split helps reject overfit strategies before they are promoted.

## Decision Logic

```mermaid
flowchart TD
    R[Backtest result] --> C{Crash?}
    C -->|yes| X[Crash: log and revert]
    C -->|no| T{Enough trades?}
    T -->|no| P[Discard: too passive]
    T -->|yes| O{Overfit?}
    O -->|yes| F[Discard: overfit]
    O -->|no| B{Best Sharpe improved?}
    B -->|yes| K[Keep and advance branch]
    B -->|no| D[Discard and revert]
```



## Evaluation Timeline

The evaluator uses one continuous historical window, but scores it in two pieces.

```mermaid
flowchart LR
    A[First 80 percent of candles<br/>train segment] --> B[sharpe_ratio<br/>primary keep or discard metric]
    A --> C[Strategy exploration]
    D[Last 20 percent of candles<br/>test segment] --> E[sharpe_test<br/>overfitting check]
    C --> D
```



## Files In Motion

- `strategy.py` changes every experiment.
- `run.log` captures the last backtest output.
- `results.tsv` is a persistent local ledger across runs; each row includes a date-based `run_tag` so histories remain interpretable as the backtest window moves.
- `backtest.py`, `exchange.py`, and `program.md` act as the stable operating environment.

## Promotion Rule

A strategy should only graduate from Phase 2 when it shows:

- solid train Sharpe,
- non-collapsing test Sharpe,
- enough activity to be meaningful,
- and behavior that still makes sense when checked in shadow mode.

