"""Minute-aware trend-following strategy with pullback entries."""

from dataclasses import dataclass

import numpy as np


@dataclass
class Signal:
    action: str
    size: float


class Strategy:
    def __init__(self):
        self.trade_size = 0.0012
        self.warmup = 240

        self.cooldown_minutes = 45
        self.max_hold_minutes = 360
        self.min_hold_minutes = 20

        self.trend_window = 180
        self.fast_window = 30
        self.entry_window = 90
        self.vol_window = 120

        self.stop_loss = -0.0065
        self.take_profit = 0.012
        self.trail_trigger = 0.006
        self.trail_gap = 0.0035

        self.entry_price = None
        self.entry_minute = None
        self.highest_price = None
        self.last_exit_minute = -10_000

    def on_tick(self, tick):
        prices = tick["history"]
        if len(prices) < self.warmup:
            return None

        price = float(tick["price"])
        position = float(tick["position"])
        minute = int(tick["elapsed"] // 60)

        if position > 0 and self.entry_price is not None:
            return self._manage_position(tick, prices, minute)

        if position > 0:
            return None

        if minute - self.last_exit_minute < self.cooldown_minutes:
            return None

        window = np.asarray(prices[-self.trend_window:], dtype=float)
        fast = float(np.mean(window[-self.fast_window:]))
        slow = float(np.mean(window))
        entry_mean = float(np.mean(prices[-self.entry_window:]))

        returns = np.diff(prices[-self.vol_window:]) / np.asarray(prices[-self.vol_window:-1])
        realized_vol = float(np.std(returns)) if len(returns) else 0.0

        trend_strength = price / slow - 1.0
        short_momentum = price / prices[-30] - 1.0
        medium_momentum = price / prices[-120] - 1.0
        pullback = price / max(prices[-45:]) - 1.0
        distance_from_mean = price / entry_mean - 1.0
        spread_bps = tick["spread"] / price * 10_000

        trend_ok = fast > slow and trend_strength > 0.0025 and medium_momentum > 0.004
        pullback_ok = -0.012 < pullback < -0.0015 and distance_from_mean < -0.002
        momentum_ok = short_momentum > -0.003
        vol_ok = 0.0008 < realized_vol < 0.006
        liquidity_ok = spread_bps <= 15.0

        if trend_ok and pullback_ok and momentum_ok and vol_ok and liquidity_ok:
            return Signal("buy", self.trade_size)

        return None

    def _manage_position(self, tick, prices, minute):
        price = float(tick["price"])
        hold_minutes = minute - self.entry_minute
        pnl_pct = price / self.entry_price - 1.0

        self.highest_price = max(self.highest_price, price)

        if pnl_pct <= self.stop_loss:
            return Signal("sell", tick["position"])

        if pnl_pct >= self.take_profit:
            return Signal("sell", tick["position"])

        if pnl_pct >= self.trail_trigger:
            trail_floor = self.highest_price * (1.0 - self.trail_gap)
            if price <= trail_floor:
                return Signal("sell", tick["position"])

        if hold_minutes < self.min_hold_minutes:
            return None

        fast = float(np.mean(prices[-20:]))
        medium = float(np.mean(prices[-60:]))
        short_momentum = price / prices[-15] - 1.0

        if price < medium and short_momentum < -0.0025:
            return Signal("sell", tick["position"])

        if fast < medium and pnl_pct > 0.001:
            return Signal("sell", tick["position"])

        if hold_minutes >= self.max_hold_minutes:
            return Signal("sell", tick["position"])

        return None

    def on_fill(self, fill):
        if fill["action"] == "buy":
            self.entry_price = fill["price"]
            self.entry_minute = int(fill["timestamp"] // 60)
            self.highest_price = fill["price"]
            return

        self.last_exit_minute = int(fill["timestamp"] // 60)
        self.entry_price = None
        self.entry_minute = None
        self.highest_price = None
