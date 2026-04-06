"""Active BTC/EUR rotation strategy using trend and z-score signals."""

from dataclasses import dataclass

import numpy as np


@dataclass
class Signal:
    action: str
    size: float


class Strategy:
    def __init__(self):
        self.trade_size = 0.00125
        self.warmup = 720

        self.cooldown_minutes = 360
        self.max_hold_minutes = 1440
        self.min_hold_minutes = 180

        self.fast_window = 60
        self.slow_window = 720
        self.mean_window = 180
        self.vol_window = 360
        self.breakout_window = 720

        self.stop_loss = -0.015
        self.take_profit = 0.030
        self.trail_trigger = 0.015
        self.trail_gap = 0.007

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
        entry_std = float(np.std(prices_arr[-self.mean_window:]))

        returns = np.diff(prices_arr[-self.vol_window:]) / np.maximum(
            prices_arr[-self.vol_window:-1], 1e-9
        )
        realized_vol = float(np.std(returns)) if len(returns) else 0.0

        short_momentum = price / prices_arr[-60] - 1.0
        medium_momentum = price / prices_arr[-240] - 1.0
        long_momentum = price / prices_arr[-720] - 1.0
        distance_from_mean = price / entry_mean - 1.0
        zscore = (price - entry_mean) / max(entry_std, 1e-9)
        spread_bps = tick["spread"] / price * 10_000
        breakout_level = float(np.max(prices_arr[-self.breakout_window:-15]))
        trend_strength = fast / slow - 1.0

        trend_buy = (
            trend_strength > 0.004
            and medium_momentum > 0.010
            and short_momentum > -0.004
            and -1.4 < zscore < -0.25
        )
        dip_buy = (
            zscore < -1.55
            and trend_strength > 0.002
            and medium_momentum > 0.006
            and short_momentum > -0.005
        )
        breakout_buy = (
            price >= breakout_level * 1.001
            and medium_momentum > 0.012
            and short_momentum > 0.004
        )
        value_buy = long_momentum > 0.02 and distance_from_mean < -0.007 and medium_momentum > 0.008
        vol_ok = 0.0005 < realized_vol < 0.0045
        liquidity_ok = spread_bps <= 15.0

        if liquidity_ok and vol_ok and (trend_buy or dip_buy or breakout_buy or value_buy):
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
        fast = float(np.mean(prices_arr[-60:]))
        medium = float(np.mean(prices_arr[-180:]))
        slow = float(np.mean(prices_arr[-720:]))
        medium_std = float(np.std(prices_arr[-180:]))
        short_momentum = price / prices_arr[-60] - 1.0
        medium_momentum = price / prices_arr[-240] - 1.0
        mean_distance = price / medium - 1.0
        zscore = (price - medium) / max(medium_std, 1e-9)

        if price < medium and short_momentum < -0.008:
            return Signal("sell", tick["position"])

        if fast < slow * 0.999 and medium_momentum < -0.002:
            return Signal("sell", tick["position"])

        if zscore > 1.60 and pnl_pct > 0.004:
            return Signal("sell", tick["position"])

        if mean_distance > 0.020 and pnl_pct > 0.006:
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
