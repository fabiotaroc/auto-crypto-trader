"""
Historical backtesting engine for autotrader experiments.

Downloads and caches BTC/EUR 1-minute OHLC data from Kraken's public API,
replays it through the Strategy class with simulated execution, and
evaluates performance using annualized Sharpe ratio.

This file is READ-ONLY — the agent never modifies it.

Usage:
    uv run backtest.py                # full backtest (90 days default)
    uv run backtest.py --days 30      # shorter window
    uv run backtest.py --update-data  # download/update cached data and exit
"""

import argparse
import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Constants (fixed, do not modify)
# ---------------------------------------------------------------------------

TRADING_PAIR = "XBTEUR"
KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

DATA_DIR = Path("data")
DATA_FILE = DATA_DIR / "btceur_1m.csv"

DEFAULT_DAYS = 90
TEST_FRACTION = 0.20
MINUTES_PER_YEAR = 525_960       # 365.25 * 24 * 60 (crypto trades 24/7)

SHADOW_START_CASH = 500.0
SHADOW_SLIPPAGE_BPS = 5          # 0.05%
SHADOW_FEE_BPS = 26              # 0.26% (Kraken taker default)
MAX_POSITION_EUR = 100
MIN_ORDER_SIZE_BTC = 0.0001
SPREAD_BPS = 10                  # estimated bid-ask spread for synthetic ticks


# ---------------------------------------------------------------------------
# Data download & caching
# ---------------------------------------------------------------------------

def _ts_to_str(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts))


def _fetch_ohlc_page(since: int | None = None) -> tuple[list, int]:
    """Fetch one page of 1-minute OHLC candles from Kraken public API."""
    params = f"pair={TRADING_PAIR}&interval=1"
    if since is not None:
        params += f"&since={since}"
    url = f"{KRAKEN_OHLC_URL}?{params}"

    req = urllib.request.Request(url, headers={"User-Agent": "autotrader/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())

    if body.get("error"):
        raise RuntimeError(f"Kraken API error: {body['error']}")

    result = body["result"]
    candle_key = [k for k in result if k != "last"][0]
    return result[candle_key], int(result["last"])


def _load_cache() -> dict[int, list[str]]:
    """Load existing CSV cache into {timestamp: [row_values]} dict."""
    rows: dict[int, list[str]] = {}
    if not DATA_FILE.exists():
        return rows
    with open(DATA_FILE, "r") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) >= 8:
                rows[int(row[0])] = row
    return rows


def _write_cache(rows: dict[int, list[str]]):
    DATA_DIR.mkdir(exist_ok=True)
    with open(DATA_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "open", "high", "low", "close",
            "vwap", "volume", "count",
        ])
        for ts in sorted(rows):
            writer.writerow(rows[ts])


def _download_range(
    since: int, until: int, rows: dict[int, list[str]],
) -> int:
    """Download candles in (since, until) and merge into rows."""
    added = 0
    page = 0
    while since < until:
        try:
            candles, last_ts = _fetch_ohlc_page(since)
        except Exception as e:
            print(f"  Download error (page {page}): {e}", file=sys.stderr)
            break
        if not candles:
            break
        for c in candles:
            ts = int(c[0])
            if ts not in rows and ts < until:
                rows[ts] = [str(c[0])] + [str(v) for v in c[1:]]
                added += 1
        page += 1
        if page % 20 == 0:
            print(
                f"  Page {page}: {added} new candles, "
                f"up to {_ts_to_str(last_ts)}",
                file=sys.stderr,
            )
        if last_ts <= since:
            break
        since = last_ts
        time.sleep(1.0)
    return added


def download_data(target_days: int = DEFAULT_DAYS):
    """Download / update cached 1-minute OHLC candles from Kraken."""
    rows = _load_cache()
    now = int(time.time())
    target_start = now - target_days * 86400

    if rows:
        earliest, latest = min(rows), max(rows)
        print(
            f"Cache: {len(rows)} candles, "
            f"{_ts_to_str(earliest)} to {_ts_to_str(latest)}",
            file=sys.stderr,
        )
        if earliest > target_start:
            print(f"Backfilling to {_ts_to_str(target_start)}...", file=sys.stderr)
            n = _download_range(target_start, earliest, rows)
            print(f"  Backfilled {n} candles", file=sys.stderr)
        print("Updating to present...", file=sys.stderr)
        n = _download_range(latest, now, rows)
        print(f"  Added {n} new candles", file=sys.stderr)
    else:
        print(
            f"No cache. Downloading {target_days} days "
            f"from {_ts_to_str(target_start)}...",
            file=sys.stderr,
        )
        n = _download_range(target_start, now, rows)
        print(f"  Downloaded {n} candles", file=sys.stderr)

    _write_cache(rows)
    print(f"Total cached: {len(rows)} candles", file=sys.stderr)


def load_candles(days: int = DEFAULT_DAYS) -> np.ndarray:
    """Load candles from CSV cache, filtered to the last *days* days.

    Returns ndarray shape (N, 8):
        [timestamp, open, high, low, close, vwap, volume, count]
    """
    if not DATA_FILE.exists():
        print("No cached data. Downloading...", file=sys.stderr)
        download_data(target_days=days)

    cutoff = time.time() - days * 86400
    data: list[list[float]] = []
    with open(DATA_FILE, "r") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if len(row) >= 8 and float(row[0]) >= cutoff:
                data.append([float(v) for v in row[:8]])

    if not data:
        print(
            f"No candles in last {days} days. "
            f"Run with --update-data.",
            file=sys.stderr,
        )
        sys.exit(1)

    arr = np.array(data)
    print(
        f"Loaded {len(arr)} candles ({days}d): "
        f"{_ts_to_str(int(arr[0, 0]))} to {_ts_to_str(int(arr[-1, 0]))}",
        file=sys.stderr,
    )
    return arr


# ---------------------------------------------------------------------------
# Backtest execution engine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """Simulates order execution against historical prices with slippage & fees.

    Similar to ShadowEngine in exchange.py but without per-cycle trade/loss
    caps (a backtest spans weeks or months, not a single 10-minute cycle).
    """

    def __init__(self):
        self.cash: float = SHADOW_START_CASH
        self.position: float = 0.0
        self.avg_entry_price: float = 0.0
        self.fills: list[dict] = []
        self.trade_pnls: list[float] = []
        self.equity_curve: list[float] = [SHADOW_START_CASH]
        self.peak_equity: float = SHADOW_START_CASH
        self.max_drawdown: float = 0.0
        self.ticks_total: int = 0
        self.ticks_with_position: int = 0

    def execute(
        self, signal, bid: float, ask: float, ts: float,
    ) -> dict | None:
        action = signal.action.lower()
        size = float(signal.size)
        if size < MIN_ORDER_SIZE_BTC or action not in ("buy", "sell"):
            return None
        if action == "buy":
            return self._buy(size, ask, ts)
        return self._sell(size, bid, ts)

    def update_equity(self, price: float):
        equity = self.cash + self.position * price
        self.equity_curve.append(equity)
        if equity > self.peak_equity:
            self.peak_equity = equity
        dd = self.peak_equity - equity
        if dd > self.max_drawdown:
            self.max_drawdown = dd

    def unrealized_pnl(self, price: float) -> float:
        if self.position < 1e-12:
            return 0.0
        return self.position * (price - self.avg_entry_price)

    def force_close(self, bid: float, ts: float):
        if self.position < MIN_ORDER_SIZE_BTC:
            self.position = 0.0
            return
        ep = bid * (1 - SHADOW_SLIPPAGE_BPS / 10_000)
        proceeds = self.position * ep
        fee = proceeds * SHADOW_FEE_BPS / 10_000
        rpnl = self.position * (ep - self.avg_entry_price) - fee
        self.trade_pnls.append(rpnl)
        self.cash += proceeds - fee
        self._append_fill("sell", ep, self.position, fee, ts)
        self.position = 0.0

    # -- private helpers -----------------------------------------------------

    def _buy(self, size: float, ask: float, ts: float) -> dict | None:
        ep = ask * (1 + SHADOW_SLIPPAGE_BPS / 10_000)

        if (self.position + size) * ep > MAX_POSITION_EUR:
            size = max(0.0, MAX_POSITION_EUR / ep - self.position)
            if size < MIN_ORDER_SIZE_BTC:
                return None

        cost = size * ep
        fee = cost * SHADOW_FEE_BPS / 10_000
        if cost + fee > self.cash:
            size = self.cash / (ep * (1 + SHADOW_FEE_BPS / 10_000))
            if size < MIN_ORDER_SIZE_BTC:
                return None
            cost = size * ep
            fee = cost * SHADOW_FEE_BPS / 10_000

        self.cash -= cost + fee
        if self.position == 0:
            self.avg_entry_price = ep
        else:
            self.avg_entry_price = (
                self.position * self.avg_entry_price + size * ep
            ) / (self.position + size)
        self.position += size
        return self._append_fill("buy", ep, size, fee, ts)

    def _sell(self, size: float, bid: float, ts: float) -> dict | None:
        if self.position <= 0:
            return None
        size = min(size, self.position)
        if size < MIN_ORDER_SIZE_BTC:
            return None

        ep = bid * (1 - SHADOW_SLIPPAGE_BPS / 10_000)
        proceeds = size * ep
        fee = proceeds * SHADOW_FEE_BPS / 10_000
        rpnl = size * (ep - self.avg_entry_price) - fee
        self.trade_pnls.append(rpnl)
        self.cash += proceeds - fee
        self.position -= size
        return self._append_fill("sell", ep, size, fee, ts)

    def _append_fill(self, action, price, size, fee, ts) -> dict:
        fill = {
            "timestamp": ts,
            "action": action,
            "price": price,
            "size": size,
            "cost": size * price,
            "fee": fee,
            "position_after": self.position,
            "cash_after": self.cash,
        }
        self.fills.append(fill)
        return fill


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _sharpe(equity: np.ndarray) -> float:
    """Annualized Sharpe ratio from a per-minute equity curve."""
    if len(equity) < 2:
        return 0.0
    rets = np.diff(equity) / np.maximum(equity[:-1], 1e-10)
    std = float(np.std(rets))
    if std < 1e-15:
        return 0.0
    return float(np.mean(rets) / std * np.sqrt(MINUTES_PER_YEAR))


def compute_metrics(
    engine: BacktestEngine, candles: np.ndarray, split_idx: int,
) -> dict:
    """Compute Sharpe (train & test) and secondary metrics."""
    eq = np.array(engine.equity_curve)

    # Equity has len(candles)+1 entries (initial value + one per candle,
    # possibly +1 more from force_close).  split_idx is the first *test*
    # candle, so equity index split_idx corresponds to end-of-train.
    train_eq = eq[: split_idx + 1]
    test_eq = eq[split_idx:]

    train_sharpe = _sharpe(train_eq)
    test_sharpe = _sharpe(test_eq) if len(test_eq) > 1 else 0.0

    pnls = engine.trade_pnls
    n = len(pnls)
    if n < 3:
        profit_factor = 0.0
    else:
        gp = sum(p for p in pnls if p > 0)
        gl = sum(abs(p) for p in pnls if p < 0)
        if gl == 0:
            profit_factor = 10.0 if gp > 0 else 0.0
        else:
            profit_factor = min(10.0, gp / gl)

    total_pnl = engine.cash - SHADOW_START_CASH
    win_rate = (sum(1 for p in pnls if p > 0) / n) if n > 0 else 0.0
    avg_pnl = (sum(pnls) / n) if n > 0 else 0.0
    tim = (
        engine.ticks_with_position / engine.ticks_total
        if engine.ticks_total > 0 else 0.0
    )

    total_sec = float(candles[-1, 0] - candles[0, 0])
    train_sec = (
        float(candles[split_idx - 1, 0] - candles[0, 0])
        if split_idx > 0 else 0.0
    )
    test_sec = total_sec - train_sec

    return {
        "sharpe_ratio": train_sharpe,
        "sharpe_test": test_sharpe,
        "profit_factor": profit_factor,
        "total_pnl_eur": total_pnl,
        "num_trades": len(engine.fills),
        "max_drawdown_eur": engine.max_drawdown,
        "win_rate": win_rate,
        "avg_trade_pnl": avg_pnl,
        "time_in_market": tim,
        "backtest_days": total_sec / 86400,
        "train_days": train_sec / 86400,
        "test_days": test_sec / 86400,
        "mode": "backtest",
    }


def print_metrics(m: dict):
    """Print metrics in a grep-friendly format."""
    print("---")
    print(f"sharpe_ratio:     {m['sharpe_ratio']:.2f}")
    print(f"sharpe_test:      {m['sharpe_test']:.2f}")
    print(f"profit_factor:    {m['profit_factor']:.6f}")
    print(f"total_pnl_eur:    {m['total_pnl_eur']:.2f}")
    print(f"num_trades:       {m['num_trades']}")
    print(f"max_drawdown_eur: {m['max_drawdown_eur']:.2f}")
    print(f"win_rate:         {m['win_rate']:.3f}")
    print(f"avg_trade_pnl:    {m['avg_trade_pnl']:.4f}")
    print(f"time_in_market:   {m['time_in_market']:.2f}")
    print(f"backtest_days:    {m['backtest_days']:.1f}")
    print(f"train_days:       {m['train_days']:.1f}")
    print(f"test_days:        {m['test_days']:.1f}")
    print(f"mode:             {m['mode']}")


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def run_backtest(candles: np.ndarray, strategy_class) -> dict:
    """Replay historical candles through a strategy and return metrics."""
    engine = BacktestEngine()
    strategy = strategy_class()
    history: list[float] = []
    start_ts = float(candles[0, 0])
    n = len(candles)

    split_idx = max(1, min(n - 1, int(n * (1 - TEST_FRACTION))))
    vol_window = 1440  # rolling 24h volume (1440 one-minute candles)
    milestones = {int(n * f): f for f in (0.25, 0.50, 0.75)}

    for i in range(n):
        row = candles[i]
        ts, c = float(row[0]), float(row[4])
        spread = c * SPREAD_BPS / 10_000
        bid, ask = c - spread / 2, c + spread / 2

        vol_start = max(0, i - vol_window)
        vol_24h = float(np.sum(candles[vol_start : i + 1, 6]))

        history.append(c)
        engine.ticks_total += 1
        if engine.position >= MIN_ORDER_SIZE_BTC:
            engine.ticks_with_position += 1

        tick = {
            "timestamp": ts,
            "price": c,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "volume_24h": vol_24h,
            "history": history,
            "elapsed": ts - start_ts,
            "position": engine.position,
            "pnl": engine.unrealized_pnl(c),
            "cash": engine.cash,
            "fills": engine.fills,
        }

        try:
            signal = strategy.on_tick(tick)
        except Exception as e:
            print(
                f"on_tick error at {_ts_to_str(int(ts))}: {e}",
                file=sys.stderr,
            )
            signal = None

        if signal is not None:
            fill = engine.execute(signal, bid, ask, ts)
            if fill is not None:
                try:
                    strategy.on_fill(fill)
                except Exception as e:
                    print(f"on_fill error: {e}", file=sys.stderr)

        engine.update_equity(c)

        if i in milestones:
            pnl = engine.cash + engine.position * c - SHADOW_START_CASH
            print(
                f"  [{int(milestones[i] * 100):>3d}%] "
                f"pnl={pnl:+.2f} trades={len(engine.fills)}",
                file=sys.stderr,
            )

    # Force-close any remaining position
    if engine.position >= MIN_ORDER_SIZE_BTC:
        last_c = float(candles[-1, 4])
        bid = last_c - last_c * SPREAD_BPS / 10_000 / 2
        engine.force_close(bid, float(candles[-1, 0]))
        engine.update_equity(last_c)

    return compute_metrics(engine, candles, split_idx)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="autotrader historical backtesting engine",
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS,
        help=f"backtest window in days (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--update-data", action="store_true",
        help="download/update historical data and exit",
    )
    args = parser.parse_args()

    if args.update_data or not DATA_FILE.exists():
        download_data(target_days=args.days)
        if args.update_data:
            sys.exit(0)

    candles = load_candles(days=args.days)

    from strategy import Strategy

    t0 = time.time()
    metrics = run_backtest(candles, Strategy)
    elapsed = time.time() - t0

    print(
        f"Backtest completed in {elapsed:.1f}s ({len(candles)} candles)",
        file=sys.stderr,
    )
    print_metrics(metrics)
