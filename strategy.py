"""Baseline: simple momentum crossover."""

from dataclasses import dataclass


@dataclass
class Signal:
    action: str   # "buy" or "sell"
    size: float   # BTC amount


class Strategy:
    def __init__(self):
        self.fast_window = 20   # ~20 seconds
        self.slow_window = 60   # ~60 seconds
        self.prices = []
        self.position_size = 0.001  # BTC per trade

    def on_tick(self, tick):
        self.prices.append(tick["price"])
        if len(self.prices) < self.slow_window:
            return None

        fast_ma = sum(self.prices[-self.fast_window:]) / self.fast_window
        slow_ma = sum(self.prices[-self.slow_window:]) / self.slow_window

        if fast_ma > slow_ma and tick["position"] <= 0:
            return Signal("buy", self.position_size)
        elif fast_ma < slow_ma and tick["position"] >= 0:
            return Signal("sell", self.position_size)
        return None

    def on_fill(self, fill):
        pass
