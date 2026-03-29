# autotrader

Give an AI agent a trading strategy and let it experiment autonomously. It modifies the strategy, runs a 10-minute live-data trading cycle in shadow mode (simulated execution against real Kraken BTC/EUR prices), checks if profit factor improved, keeps or discards, and repeats. You wake up in the morning to a log of experiments and (hopefully) a profitable algorithm.

Adapted from the [autoresearch](https://github.com/karpathy/autoresearch) feedback loop — same hill-climbing structure, but applied to algorithmic trading instead of neural network training.

## How it works

Three files that matter:

- **`exchange.py`** — fixed infrastructure: Kraken public WebSocket feed, shadow execution engine (simulated fills with realistic slippage + fees), evaluation harness, safety rails. Not modified by the agent.
- **`strategy.py`** — the single file the agent edits. Trading signals, entry/exit logic, position sizing. Everything is fair game: indicators, risk management, regime detection, ensembles. Must maintain the `Strategy` class interface (`__init__`, `on_tick`, `on_fill`).
- **`program.md`** — agent instructions. Point your agent here and let it go. Edited by the human.

Each experiment runs for a **fixed 10-minute window** against real-time BTC/EUR prices from Kraken's public WebSocket. The metric is **profit factor** (gross profit / gross loss) — higher is better. Above 1.0 is profitable, above 1.75 is strong.

Shadow mode simulates realistic conditions: 0.26% taker fees (Kraken default), 0.05% slippage, position limits (€100 max), and per-cycle loss limits (€10). No API key needed — only public market data.

## Quick start

**Requirements:** Python 3.10+, [uv](https://docs.astral.sh/uv/), internet connection. No GPU needed.

```bash
# Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Run one 10-minute shadow trading cycle
uv run exchange.py

# Or a shorter cycle for testing (60 seconds)
uv run exchange.py --duration 60
```

## Running the agent

Spin up Claude/Codex (or your preferred agent) in this repo, then prompt:

```
Hi, have a look at program.md and let's kick off a new experiment!
```

The agent reads `program.md`, creates an experiment branch, and begins the autonomous loop: modify strategy → run cycle → evaluate → keep or discard → repeat.

## Project structure

```
exchange.py      — Kraken WS feed, shadow engine, evaluation (read-only)
strategy.py      — trading strategy (agent modifies this)
program.md       — agent instructions
BRAINSTORM.md    — full design document and rationale
pyproject.toml   — dependencies
results.tsv      — experiment log (untracked)
```

## Design choices

- **Single file to modify.** The agent only touches `strategy.py`. Diffs are reviewable and experiments are isolated.
- **Fixed time budget.** Every experiment runs for exactly 10 minutes (~6 experiments/hour, ~50 overnight). Results are directly comparable regardless of what the agent changes.
- **Real market data, simulated execution.** Prices come from Kraken's live WebSocket feed. Execution is simulated internally because Kraken doesn't offer spot paper trading. The shadow engine models slippage, fees, and position limits so strategies that graduate to live mode face no surprises.
- **Defense in depth.** Hard limits in `exchange.py` (position size, loss per cycle, trade count) cannot be overridden. Cycle isolation ensures no state leaks between experiments.
- **No GPU, no heavy deps.** Just `websockets` and `numpy`. Runs on any machine with internet.

## Roadmap

- **Phase 1** ✅ Shadow mode scaffold — exchange, strategy interface, evaluation harness
- **Phase 2** — Agent loop in shadow mode (run overnight, iterate on strategies)
- **Phase 3** — Live trading with restricted Kraken API key

## License

MIT
