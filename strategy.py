"""Momentum and mean-reversion portfolio rotation strategy."""

from dataclasses import dataclass

import numpy as np


@dataclass
class Signal:
    action: str
    size: float


class Strategy:
    def __init__(self):
        self.trade_size = 0.0012
        self.warmup = 360

        self.cooldown_minutes = 30
        self.max_hold_minutes = 360
        self.min_hold_minutes = 30

        self.fast_window = 45
        self.slow_window = 240
        self.mean_window = 90
        self.vol_window = 180

        self.stop_loss = -0.008
        self.take_profit = 0.014
        self.trail_trigger = 0.007
        self.trail_gap = 0.0045

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

        if position > 0 and self.entry_price is None:
            # The harness starts with BTC inventory, so bootstrap the trade state
            # from the current mark price to allow active rotation on day one.
            self.entry_price = price
            self.entry_minute = minute
            self.highest_price = price

        if position > 0 and self.entry_price is not None:
            return self._manage_position(tick, prices, minute)

        if position > 0:
            return None

        if minute - self.last_exit_minute < self.cooldown_minutes:
            return None

        prices_arr = np.asarray(prices, dtype=float)
        fast = float(np.mean(prices_arr[-self.fast_window:]))
        slow = float(np.mean(prices_arr[-self.slow_window:]))
        entry_mean = float(np.mean(prices_arr[-self.mean_window:]))

        returns = np.diff(prices_arr[-self.vol_window:]) / np.maximum(
            prices_arr[-self.vol_window:-1], 1e-9
        )
        realized_vol = float(np.std(returns)) if len(returns) else 0.0

        trend_strength = price / slow - 1.0
        short_momentum = price / prices_arr[-30] - 1.0
        medium_momentum = price / prices_arr[-120] - 1.0
        long_momentum = price / prices_arr[-360] - 1.0
        pullback = price / float(np.max(prices_arr[-60:])) - 1.0
        distance_from_mean = price / entry_mean - 1.0
        spread_bps = tick["spread"] / price * 10_000

        trend_ok = fast > slow and trend_strength > 0.0015
        pullback_ok = -0.015 < pullback < -0.002 and distance_from_mean < -0.001
        momentum_ok = short_momentum > -0.004 and medium_momentum > -0.002
        long_bias_ok = long_momentum > -0.01
        vol_ok = 0.0007 < realized_vol < 0.007
        liquidity_ok = spread_bps <= 15.0

        if trend_ok and pullback_ok and momentum_ok and long_bias_ok and vol_ok and liquidity_ok:
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

        prices_arr = np.asarray(prices, dtype=float)
        fast = float(np.mean(prices_arr[-30:]))
        medium = float(np.mean(prices_arr[-90:]))
        slow = float(np.mean(prices_arr[-240:]))
        short_momentum = price / prices_arr[-15] - 1.0
        medium_momentum = price / prices_arr[-60] - 1.0
        mean_distance = price / medium - 1.0

        if price < medium and short_momentum < -0.002:
            return Signal("sell", tick["position"])

        if fast < medium and medium < slow:
            return Signal("sell", tick["position"])

        if mean_distance > 0.01 and pnl_pct > 0:
            return Signal("sell", tick["position"])

        if medium_momentum < -0.006:
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
