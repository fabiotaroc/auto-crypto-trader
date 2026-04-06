# autotrader

Give an AI agent a trading strategy and let it experiment autonomously. It modifies the strategy, backtests it against months of historical BTC/EUR data in seconds, checks if the Sharpe ratio improved, keeps or discards, and repeats. You wake up in the morning to a log of experiments and (hopefully) a profitable algorithm.

Adapted from the [autoresearch](https://github.com/karpathy/autoresearch) feedback loop — same hill-climbing structure, but applied to algorithmic trading instead of neural network training.

## How it works

Four files that matter:

- `**backtest.py**` — fixed infrastructure: downloads historical 1-minute OHLC candles from Kraken's public API, replays them through the strategy with simulated execution (realistic slippage + fees), evaluates performance. Not modified by the agent.
- `**exchange.py**` — fixed infrastructure: Kraken public WebSocket feed for live shadow validation. Simulates execution against real-time prices. Not modified by the agent.
- `**strategy.py**` — the single file the agent edits. Trading signals, entry/exit logic, position sizing. Everything is fair game: indicators, risk management, regime detection, ensembles. Must maintain the `Strategy` class interface (`__init__`, `on_tick`, `on_fill`).
- `**program.md**` — agent instructions. Point your agent here and let it go. Edited by the human.

Each experiment **backtests against 90 days of 1-minute BTC/EUR candles** (configurable). The primary metric is **annualized Sharpe ratio** — higher is better. Above 1.0 is good, above 2.0 is strong. A backtest completes in seconds, enabling fast iteration.

The data is split 80/20 into train and test sets. The agent optimizes train Sharpe; test Sharpe monitors overfitting.

Execution simulation models realistic conditions: 0.26% taker fees (Kraken default), 0.05% slippage, €100 max position. No API key needed — only public market data.

## Quick start

**Requirements:** Python 3.10+, [uv](https://docs.astral.sh/uv/), internet connection. No GPU needed.

```bash
# Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Download historical data from Kraken (cached in data/)
uv run backtest.py --update-data

# Run a backtest (completes in seconds)
uv run backtest.py

# Or with a shorter window for quick tests
uv run backtest.py --days 30

# Optional: live shadow validation (2-minute cycle against real-time prices)
uv run exchange.py --duration 120
```

## Running the agent

Spin up Claude/Codex (or your preferred agent) in this repo, then prompt:

```
Hi, have a look at program.md and let's kick off a new experiment!
```

The agent reads `program.md`, creates an experiment branch, and begins the autonomous loop: modify strategy → run backtest → evaluate → keep or discard → repeat.

## Project structure

```
backtest.py      — historical replay engine, data download, evaluation (read-only)
exchange.py      — Kraken live WS feed, shadow engine (read-only, for validation)
strategy.py      — trading strategy (agent modifies this)
program.md       — agent instructions
BRAINSTORM.md    — full design document and rationale
docs/            — architecture docs and phase walkthroughs
pyproject.toml   — dependencies
data/            — cached historical OHLC candles (untracked)
results.tsv      — experiment log (untracked)
```

## Documentation

- Architecture walkthrough: `docs/system-architecture/README.md`
- Phase-by-phase system docs: `docs/system-architecture/`
- Full project plan and rationale: `BRAINSTORM.md`

## Design choices

- **Historical backtesting as primary loop.** The agent iterates against months of historical data, not live market conditions. This gives deterministic, fast, statistically robust feedback. The same strategy always produces the same result — the agent can reliably tell whether a change helped.
- **Single file to modify.** The agent only touches `strategy.py`. Diffs are reviewable and experiments are isolated.
- **Fast iteration.** Each backtest completes in seconds (~100K candles replayed). The agent can run hundreds of experiments per day, compared to ~6/hour with the old live-trading approach.
- **Train/test split.** The last 20% of historical data is held out as a test set. Train Sharpe drives keep/discard; test Sharpe catches overfitting.
- **Real market data, simulated execution.** Prices come from Kraken's public OHLC API (1-minute candles). Execution is simulated with realistic slippage, fees, and position limits so strategies that graduate to live mode face no surprises.
- **Live validation available.** `exchange.py` connects to Kraken's real-time WebSocket for optional shadow validation against current market conditions.
- **Defense in depth.** Hard limits (position size, loss per cycle, trade count) cannot be overridden. The backtest and live engines share the same execution constants.
- **No GPU, no heavy deps.** Just `websockets` and `numpy`. Runs on any machine with internet.

## Roadmap

- **Phase 1** ✅ Infrastructure scaffold — backtest engine, live shadow engine, strategy interface, evaluation harness
- **Phase 2** — Agent loop with backtesting (run overnight, iterate on strategies)
- **Phase 3** — Live trading with restricted Kraken API key

For the detailed project plan behind these phases, see `BRAINSTORM.md`.

## License

MIT