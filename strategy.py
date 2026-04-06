"""Risk-off BTC/EUR rotation with tiny uptrend pullback entries."""

from dataclasses import dataclass

import numpy as np


@dataclass
class Signal:
    action: str
    size: float


class Strategy:
    def __init__(self):
        self.trade_size = 0.0001
        self.warmup = 1440

        self.cooldown_minutes = 720
        self.max_hold_minutes = 1440
        self.min_hold_minutes = 180

        self.fast_window = 120
        self.medium_window = 360
        self.slow_window = 1440
        self.mean_window = 240
        self.vol_window = 360
        self.breakout_window = 720
        self.rsi_window = 180

        self.stop_loss = -0.010
        self.take_profit = 0.030
        self.trail_trigger = 0.015
        self.trail_gap = 0.008

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
        minute = int(tick["timestamp"] // 60)

        if position > 0 and self.entry_price is None:
            # The harness starts with BTC inventory. Seed it as an existing
            # position, but allow an immediate regime-based exit.
            self.entry_price = price
            self.entry_minute = minute - self.min_hold_minutes
            self.highest_price = price

        if position > 0 and self.entry_price is not None:
            return self._manage_position(tick, prices, minute)

        if position > 0:
            return None

        if minute - self.last_exit_minute < self.cooldown_minutes:
            return None

        prices_arr = np.asarray(prices, dtype=float)
        fast = float(np.mean(prices_arr[-self.fast_window:]))
        medium = float(np.mean(prices_arr[-self.medium_window:]))
        slow = float(np.mean(prices_arr[-self.slow_window:]))
        reentry_mean = float(np.mean(prices_arr[-self.mean_window:]))
        reentry_std = float(np.std(prices_arr[-self.mean_window:]))

        returns = np.diff(prices_arr[-self.vol_window:]) / np.maximum(
            prices_arr[-self.vol_window:-1], 1e-9
        )
        realized_vol = float(np.std(returns)) if len(returns) else 0.0

        short_momentum = price / prices_arr[-60] - 1.0
        medium_momentum = price / prices_arr[-360] - 1.0
        day_momentum = price / prices_arr[-1440] - 1.0
        distance_from_mean = price / reentry_mean - 1.0
        zscore = (price - reentry_mean) / max(reentry_std, 1e-9)
        breakout_level = float(np.max(prices_arr[-self.breakout_window:-60]))
        spread_bps = tick["spread"] / price * 10_000
        rsi = self._rsi(prices_arr[-self.rsi_window:])

        regime_ok = price > slow and medium > slow and day_momentum > 0.0
        trend_ok = fast > slow * 1.0005 and medium_momentum > 0.004
        pullback_entry = (
            -0.015 < distance_from_mean < -0.004
            and -2.0 < zscore < -0.45
            and -0.009 < short_momentum < 0.001
            and price < breakout_level * 0.997
        )
        rsi_ok = 34.0 <= rsi <= 52.0
        vol_ok = 0.00045 < realized_vol < 0.0035
        liquidity_ok = spread_bps <= 15.0

        if regime_ok and trend_ok and pullback_entry and rsi_ok and vol_ok and liquidity_ok:
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
            prices_arr = np.asarray(prices, dtype=float)
            slow = float(np.mean(prices_arr[-self.slow_window:]))
            day_momentum = price / prices_arr[-1440] - 1.0
            if price < slow and day_momentum < -0.01:
                return Signal("sell", tick["position"])
            return None

        prices_arr = np.asarray(prices, dtype=float)
        fast = float(np.mean(prices_arr[-self.fast_window:]))
        medium = float(np.mean(prices_arr[-self.medium_window:]))
        slow = float(np.mean(prices_arr[-self.slow_window:]))
        medium_std = float(np.std(prices_arr[-self.mean_window:]))
        short_momentum = price / prices_arr[-60] - 1.0
        medium_momentum = price / prices_arr[-360] - 1.0
        day_momentum = price / prices_arr[-1440] - 1.0
        mean_distance = price / medium - 1.0
        zscore = (price - medium) / max(medium_std, 1e-9)
        rsi = self._rsi(prices_arr[-self.rsi_window:])

        if price < slow and day_momentum < -0.006:
            return Signal("sell", tick["position"])

        if price < medium and short_momentum < -0.007:
            return Signal("sell", tick["position"])

        if fast < medium and medium_momentum < -0.004:
            return Signal("sell", tick["position"])

        if zscore > 0.85 and pnl_pct > 0.002:
            return Signal("sell", tick["position"])

        if rsi > 63.0 and pnl_pct > 0:
            return Signal("sell", tick["position"])

        if mean_distance > 0.008 and pnl_pct > 0.002:
            return Signal("sell", tick["position"])

        if hold_minutes >= self.max_hold_minutes:
            return Signal("sell", tick["position"])

        return None

    def _rsi(self, prices):
        deltas = np.diff(prices)
        if len(deltas) == 0:
            return 50.0
        gains = np.clip(deltas, 0.0, None)
        losses = np.clip(-deltas, 0.0, None)
        avg_gain = float(np.mean(gains))
        avg_loss = float(np.mean(losses))
        if avg_loss < 1e-12:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

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
