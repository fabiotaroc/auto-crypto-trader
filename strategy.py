"""Fee-aware long-only momentum strategy."""

from dataclasses import dataclass

import numpy as np


@dataclass
class Signal:
    action: str
    size: float


class Strategy:
    def __init__(self):
        self.trade_size = 0.0015
        self.min_history = 120
        self.cooldown_seconds = 20.0
        self.max_hold_seconds = 150.0
        self.take_profit = 0.0075
        self.stop_loss = -0.0040
        self.trail_trigger = 0.0045
        self.trail_gap = 0.0030

        self.entry_price = None
        self.entry_time = None
        self.highest_price = None
        self.last_exit_time = -1e9
        self.entry_mode = None
        self.pending_entry_mode = None

    def on_tick(self, tick):
        prices = tick["history"]
        if len(prices) < self.min_history:
            return None

        price = tick["price"]
        position = tick["position"]
        now = tick["timestamp"]

        if position > 0 and self.entry_price is not None:
            return self._manage_position(tick)

        if position > 0:
            return None

        if now - self.last_exit_time < self.cooldown_seconds:
            return None

        window = np.asarray(prices[-120:], dtype=float)
        fast = window[-12:].mean()
        medium = window[-40:].mean()
        slow = window.mean()

        short_returns = np.diff(window[-40:]) / window[-40:-1]
        realized_vol = float(np.std(short_returns)) if len(short_returns) else 0.0

        momentum_20 = price / prices[-20] - 1.0
        momentum_60 = price / prices[-60] - 1.0
        breakout_30 = price / max(prices[-30:]) - 1.0
        pullback_90 = price / min(prices[-90:]) - 1.0
        spread_bps = tick["spread"] / price * 10_000

        trend_ok = price > slow and fast > medium > slow
        momentum_ok = momentum_20 > max(0.0012, realized_vol * 1.8) and momentum_60 > 0.0012
        breakout_ok = breakout_30 > -0.0005 and pullback_90 > 0.0015
        liquidity_ok = spread_bps < 3.0

        if trend_ok and momentum_ok and breakout_ok and liquidity_ok:
            self.pending_entry_mode = "breakout"
            return Signal("buy", self.trade_size)

        mean = window[-60:].mean()
        std = window[-60:].std()
        zscore = (price - mean) / std if std > 1e-9 else 0.0
        bounce_setup = momentum_20 < -max(0.0018, realized_vol * 1.6) and zscore < -1.9

        if bounce_setup and liquidity_ok:
            self.pending_entry_mode = "reversion"
            return Signal("buy", self.trade_size)

        return None

    def _manage_position(self, tick):
        price = tick["price"]
        now = tick["timestamp"]
        hold_time = now - self.entry_time
        pnl_pct = price / self.entry_price - 1.0
        self.highest_price = max(self.highest_price, price)

        if pnl_pct >= self.take_profit:
            return Signal("sell", tick["position"])

        if pnl_pct <= self.stop_loss:
            return Signal("sell", tick["position"])

        if pnl_pct >= self.trail_trigger:
            trail_floor = self.highest_price * (1.0 - self.trail_gap)
            if price <= trail_floor:
                return Signal("sell", tick["position"])

        prices = tick["history"]
        fast = np.mean(prices[-15:])
        medium = np.mean(prices[-45:])
        momentum_12 = price / prices[-12] - 1.0

        if self.entry_mode == "breakout":
            if hold_time >= 25 and (price < medium or fast < medium or momentum_12 < -0.0012):
                return Signal("sell", tick["position"])
        elif hold_time >= 15:
            mean_20 = np.mean(prices[-20:])
            if price >= mean_20 or momentum_12 > 0.0015:
                return Signal("sell", tick["position"])
            if momentum_12 < -0.0018:
                return Signal("sell", tick["position"])

        if hold_time >= self.max_hold_seconds:
            return Signal("sell", tick["position"])

        return None

    def on_fill(self, fill):
        if fill["action"] == "buy":
            self.entry_price = fill["price"]
            self.entry_time = fill["timestamp"]
            self.highest_price = fill["price"]
            self.entry_mode = self.pending_entry_mode
            self.pending_entry_mode = None
            return

        self.last_exit_time = fill["timestamp"]
        self.entry_price = None
        self.entry_time = None
        self.highest_price = None
        self.entry_mode = None
        self.pending_entry_mode = None
