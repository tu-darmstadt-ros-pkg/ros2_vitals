"""Utility for calculating rates from cumulative counters."""

import time
from typing import Dict, Tuple, Optional


class RateCalculator:
    """
    Calculates rates (bytes/sec, etc.) from cumulative counter values.

    Stores previous values and timestamps to compute the rate of change.
    """

    def __init__(self):
        # key -> (timestamp, value)
        self._prev_values: Dict[str, Tuple[float, float]] = {}

    def calculate_rate(self, key: str, current_value: float) -> float:
        """
        Calculate the rate of change for a cumulative counter.

        Args:
            key: Unique identifier for this counter
            current_value: Current cumulative value

        Returns:
            Rate per second, or 0.0 if this is the first measurement
        """
        current_time = time.time()

        if key not in self._prev_values:
            self._prev_values[key] = (current_time, current_value)
            return 0.0

        prev_time, prev_value = self._prev_values[key]
        time_delta = current_time - prev_time

        if time_delta <= 0:
            return 0.0

        # Handle counter wraparound (shouldn't happen often with uint64)
        value_delta = current_value - prev_value
        if value_delta < 0:
            value_delta = current_value  # Assume reset

        rate = value_delta / time_delta

        # Update stored value
        self._prev_values[key] = (current_time, current_value)

        return rate

    def calculate_rates(self, key: str, values: Dict[str, float]) -> Dict[str, float]:
        """
        Calculate rates for multiple related counters.

        Args:
            key: Base key for this group of counters
            values: Dictionary of counter_name -> cumulative_value

        Returns:
            Dictionary of counter_name -> rate_per_second
        """
        rates = {}
        for name, value in values.items():
            full_key = f"{key}.{name}"
            rates[name] = self.calculate_rate(full_key, value)
        return rates

    def clear(self, key_prefix: Optional[str] = None):
        """
        Clear stored values.

        Args:
            key_prefix: If provided, only clear keys starting with this prefix
        """
        if key_prefix is None:
            self._prev_values.clear()
        else:
            keys_to_remove = [k for k in self._prev_values if k.startswith(key_prefix)]
            for key in keys_to_remove:
                del self._prev_values[key]

    def remove_key(self, key: str):
        """Remove a specific key from the cache."""
        self._prev_values.pop(key, None)
