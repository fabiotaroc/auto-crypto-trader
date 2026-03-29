"""
Shadow trading engine for autotrader experiments.

Connects to Kraken's public WebSocket for real-time BTC/EUR prices,
simulates order execution with realistic slippage and fees, and
evaluates strategy performance after each cycle.

This file is READ-ONLY — the agent never modifies it.

Usage:
    uv run exchange.py                # full 10-minute cycle
    uv run exchange.py --duration 60  # short cycle for testing
"""

import argparse
import asyncio
import json
import os
import sys
import time

import numpy as np
import websockets

# ---------------------------------------------------------------------------
# Constants (fixed, do not modify)
# ---------------------------------------------------------------------------

TRADING_PAIR = "BTC/EUR"
CYCLE_DURATION = 600          # 10-minute trading window (seconds)
MAX_POSITION_EUR = 100        # hard cap on position size in EUR
MAX_LOSS_PER_CYCLE_EUR = 10   # stop-loss per experiment
MAX_TRADES_PER_CYCLE = 50     # prevent runaway order spam
TICK_INTERVAL = 1.0           # strategy called every 1 second
MODE = "shadow"               # "shadow" or "live" — default shadow
SHADOW_START_CASH = 500.0     # virtual starting cash in EUR
SHADOW_SLIPPAGE_BPS = 5       # simulated slippage: 5 basis points (0.05%)
SHADOW_FEE_BPS = 26           # simulated taker fee: 26 bps (0.26%, Kraken default)
MIN_ORDER_SIZE_BTC = 0.0001   # Kraken minimum order size

KRAKEN_WS_URL = "wss://ws.kraken.com/v2"

# ---------------------------------------------------------------------------
# Market Data
# ---------------------------------------------------------------------------

class MarketData:
    """Latest market state from the Kraken WebSocket feed."""
    __slots__ = ("price", "bid", "ask", "volume_24h", "has_data")

    def __init__(self):
        self.price = 0.0
        self.bid = 0.0
        self.ask = 0.0
        self.volume_24h = 0.0
        self.has_data = False


def _process_ws_message(raw: str, market: MarketData):
    """Parse a Kraken v2 WebSocket message and update market state."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    if msg.get("channel") != "ticker":
        return

    data = msg.get("data")
    if not data or not isinstance(data, list):
        return

    for item in data:
        if item.get("symbol") != TRADING_PAIR:
            continue
        for key, attr in (("bid", "bid"), ("ask", "ask"), ("last", "price"), ("volume", "volume_24h")):
            val = item.get(key)
            if val is not None:
                setattr(market, attr, float(val))
        if market.bid > 0 and market.ask > 0 and market.price > 0:
            market.has_data = True


# ---------------------------------------------------------------------------
# Shadow Execution Engine
# ---------------------------------------------------------------------------

class ShadowEngine:
    """Simulates order execution against real bid/ask with slippage and fees."""

    def __init__(self):
        self.cash: float = SHADOW_START_CASH
        self.position: float = 0.0       # BTC held (long-only, always >= 0)
        self.avg_entry_price: float = 0.0
        self.fills: list[dict] = []
        self.trade_pnls: list[float] = []
        self.equity_curve: list[float] = [SHADOW_START_CASH]
        self.peak_equity: float = SHADOW_START_CASH
        self.max_drawdown: float = 0.0
        self.safety_stopped: bool = False

    @property
    def num_fills(self) -> int:
        return len(self.fills)

    # -- public API ----------------------------------------------------------

    def execute(self, signal, bid: float, ask: float, timestamp: float) -> dict | None:
        """Execute a signal through the shadow engine. Returns fill dict or None."""
        if self.safety_stopped or self.num_fills >= MAX_TRADES_PER_CYCLE:
            return None

        action = signal.action.lower()
        size = float(signal.size)

        if size < MIN_ORDER_SIZE_BTC or action not in ("buy", "sell"):
            return None

        if action == "buy":
            return self._buy(size, ask, timestamp)
        return self._sell(size, bid, timestamp)

    def update_equity(self, price: float):
        equity = self.cash + self.position * price
        self.equity_curve.append(equity)
        if equity > self.peak_equity:
            self.peak_equity = equity
        dd = self.peak_equity - equity
        if dd > self.max_drawdown:
            self.max_drawdown = dd

    def check_safety(self, price: float) -> bool:
        pnl = (self.cash + self.position * price) - SHADOW_START_CASH
        if pnl < -MAX_LOSS_PER_CYCLE_EUR:
            self.safety_stopped = True
            return True
        return False

    def force_close(self, bid: float, ask: float, timestamp: float):
        """Close any remaining long position (bypasses safety/trade-count limits)."""
        if self.position < MIN_ORDER_SIZE_BTC:
            self.position = 0.0
            return

        ep = bid * (1 - SHADOW_SLIPPAGE_BPS / 10_000)
        proceeds = self.position * ep
        fee = proceeds * SHADOW_FEE_BPS / 10_000
        realized = self.position * (ep - self.avg_entry_price) - fee
        self.trade_pnls.append(realized)
        self.cash += proceeds - fee
        self._append_fill("sell", ep, self.position, fee, timestamp)
        self.position = 0.0

    def unrealized_pnl(self, price: float) -> float:
        if self.position < 1e-12:
            return 0.0
        return self.position * (price - self.avg_entry_price)

    # -- private helpers -----------------------------------------------------

    def _buy(self, size: float, ask: float, ts: float) -> dict | None:
        ep = ask * (1 + SHADOW_SLIPPAGE_BPS / 10_000)

        # Clamp to position limit
        if (self.position + size) * ep > MAX_POSITION_EUR:
            size = max(0.0, MAX_POSITION_EUR / ep - self.position)
            if size < MIN_ORDER_SIZE_BTC:
                return None

        cost = size * ep
        fee = cost * SHADOW_FEE_BPS / 10_000

        # Clamp to available cash
        if cost + fee > self.cash:
            size = self.cash / (ep * (1 + SHADOW_FEE_BPS / 10_000))
            if size < MIN_ORDER_SIZE_BTC:
                return None
            cost = size * ep
            fee = cost * SHADOW_FEE_BPS / 10_000

        self.cash -= cost + fee

        # Update average entry price
        if self.position == 0:
            self.avg_entry_price = ep
        else:
            self.avg_entry_price = (
                self.position * self.avg_entry_price + size * ep
            ) / (self.position + size)

        self.position += size
        return self._append_fill("buy", ep, size, fee, ts)

    def _sell(self, size: float, bid: float, ts: float) -> dict | None:
        # Long-only: can only sell what we hold
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
# Evaluation
# ---------------------------------------------------------------------------

def compute_metrics(
    engine: ShadowEngine,
    cycle_seconds: float,
    ticks_total: int,
    ticks_with_position: int,
) -> dict:
    """Compute all performance metrics after a cycle."""
    pnls = engine.trade_pnls
    n = len(pnls)

    # Profit factor
    if n < 3:
        profit_factor = 0.0
    else:
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = sum(abs(p) for p in pnls if p < 0)
        if gross_loss == 0:
            profit_factor = 10.0 if gross_profit > 0 else 0.0
        else:
            profit_factor = min(10.0, gross_profit / gross_loss)

    total_pnl = engine.cash - SHADOW_START_CASH
    win_rate = (sum(1 for p in pnls if p > 0) / n) if n > 0 else 0.0
    avg_trade_pnl = (sum(pnls) / n) if n > 0 else 0.0

    # Sharpe from equity-curve returns
    eq = np.array(engine.equity_curve)
    if len(eq) > 1:
        rets = np.diff(eq) / np.maximum(eq[:-1], 1e-10)
        std = float(np.std(rets))
        sharpe = float(np.mean(rets) / std * np.sqrt(len(rets))) if std > 0 else 0.0
    else:
        sharpe = 0.0

    time_in_market = ticks_with_position / ticks_total if ticks_total > 0 else 0.0

    return {
        "profit_factor": profit_factor,
        "total_pnl_eur": total_pnl,
        "num_trades": engine.num_fills,
        "max_drawdown_eur": engine.max_drawdown,
        "win_rate": win_rate,
        "avg_trade_pnl": avg_trade_pnl,
        "sharpe_ratio": sharpe,
        "time_in_market": time_in_market,
        "cycle_seconds": cycle_seconds,
        "mode": MODE,
    }


def print_metrics(metrics: dict):
    """Print metrics in a grep-friendly format matching the spec."""
    print("---")
    print(f"profit_factor:    {metrics['profit_factor']:.6f}")
    print(f"total_pnl_eur:    {metrics['total_pnl_eur']:.2f}")
    print(f"num_trades:       {metrics['num_trades']}")
    print(f"max_drawdown_eur: {metrics['max_drawdown_eur']:.2f}")
    print(f"win_rate:         {metrics['win_rate']:.3f}")
    print(f"avg_trade_pnl:    {metrics['avg_trade_pnl']:.4f}")
    print(f"sharpe_ratio:     {metrics['sharpe_ratio']:.2f}")
    print(f"time_in_market:   {metrics['time_in_market']:.2f}")
    print(f"cycle_seconds:    {metrics['cycle_seconds']:.1f}")
    print(f"mode:             {metrics['mode']}")


# ---------------------------------------------------------------------------
# Cycle runner
# ---------------------------------------------------------------------------

async def run_cycle(cycle_duration: int = CYCLE_DURATION):
    """Run one trading cycle: connect, tick strategy, evaluate."""
    from strategy import Strategy

    strategy = Strategy()
    engine = ShadowEngine()
    market = MarketData()
    price_history: list[float] = []
    ticks_total = 0
    ticks_with_position = 0

    async with websockets.connect(
        KRAKEN_WS_URL, ping_interval=20, ping_timeout=10, close_timeout=5,
    ) as ws:
        # Subscribe to BTC/EUR ticker
        await ws.send(json.dumps({
            "method": "subscribe",
            "params": {"channel": "ticker", "symbol": [TRADING_PAIR]},
        }))

        # Wait for initial market data
        print(f"Connecting to Kraken ({TRADING_PAIR})...", file=sys.stderr)
        deadline = time.time() + 30
        while not market.has_data:
            if time.time() > deadline:
                print("ERROR: timeout waiting for market data", file=sys.stderr)
                sys.exit(1)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                _process_ws_message(raw, market)
            except asyncio.TimeoutError:
                pass

        print(
            f"Live feed: price={market.price:.2f} bid={market.bid:.2f} "
            f"ask={market.ask:.2f}",
            file=sys.stderr,
        )
        print(f"Starting {cycle_duration}s cycle ({MODE} mode).", file=sys.stderr)

        cycle_start = time.time()
        last_tick = cycle_start - TICK_INTERVAL  # fire first tick immediately

        while True:
            now = time.time()
            if now - cycle_start >= cycle_duration:
                break

            # Kill switch
            if os.path.exists("STOP"):
                print("Kill switch: STOP file detected", file=sys.stderr)
                break

            # Receive one WebSocket message (short timeout to stay responsive)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.05)
                _process_ws_message(raw, market)
            except asyncio.TimeoutError:
                pass
            except websockets.exceptions.ConnectionClosed:
                print("WebSocket disconnected", file=sys.stderr)
                break

            # Tick the strategy every TICK_INTERVAL
            now = time.time()
            if now - last_tick >= TICK_INTERVAL and market.has_data:
                price_history.append(market.price)
                ticks_total += 1
                if engine.position >= MIN_ORDER_SIZE_BTC:
                    ticks_with_position += 1

                tick = {
                    "timestamp": now,
                    "price": market.price,
                    "bid": market.bid,
                    "ask": market.ask,
                    "spread": market.ask - market.bid,
                    "volume_24h": market.volume_24h,
                    "history": list(price_history),
                    "elapsed": now - cycle_start,
                    "position": engine.position,
                    "pnl": engine.unrealized_pnl(market.price),
                    "cash": engine.cash,
                    "fills": list(engine.fills),
                }

                try:
                    signal = strategy.on_tick(tick)
                except Exception as e:
                    print(f"Strategy.on_tick error: {e}", file=sys.stderr)
                    signal = None

                if signal is not None:
                    fill = engine.execute(signal, market.bid, market.ask, now)
                    if fill is not None:
                        try:
                            strategy.on_fill(fill)
                        except Exception as e:
                            print(f"Strategy.on_fill error: {e}", file=sys.stderr)

                engine.update_equity(market.price)

                if engine.check_safety(market.price):
                    print("Safety stop: max cycle loss exceeded", file=sys.stderr)
                    break

                # Progress update every 60 ticks (~60 seconds)
                if ticks_total % 60 == 0:
                    pnl = engine.cash + engine.position * market.price - SHADOW_START_CASH
                    print(
                        f"  [{int(now - cycle_start):>3d}s] "
                        f"price={market.price:.2f} pos={engine.position:+.6f} "
                        f"pnl={pnl:+.2f} trades={engine.num_fills}",
                        file=sys.stderr,
                    )

                last_tick = now

        # End of cycle: force-close any open position
        if market.has_data and engine.position >= MIN_ORDER_SIZE_BTC:
            print(
                f"Closing position: {engine.position:+.6f} BTC",
                file=sys.stderr,
            )
            engine.force_close(market.bid, market.ask, time.time())
            engine.update_equity(market.price)

        actual_duration = time.time() - cycle_start
        metrics = compute_metrics(
            engine, actual_duration, ticks_total, ticks_with_position,
        )
        print_metrics(metrics)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="autotrader shadow trading engine")
    parser.add_argument(
        "--duration", type=int, default=CYCLE_DURATION,
        help=f"cycle duration in seconds (default: {CYCLE_DURATION})",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run_cycle(args.duration))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(0)
