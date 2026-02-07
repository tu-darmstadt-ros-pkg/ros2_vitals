"""Collector for network interface statistics."""

from typing import List, Dict, Any

import psutil

from ..utils.rate_calculator import RateCalculator


class NetworkCollector:
    """Collects network interface statistics and calculates transfer rates."""

    def __init__(self):
        self._rate_calc = RateCalculator()

    def get_interfaces(self) -> List[Dict[str, Any]]:
        """
        Get statistics for all network interfaces.

        Returns:
            List of dictionaries with interface statistics
        """
        interfaces = []

        # Get interface stats
        io_counters = psutil.net_io_counters(pernic=True)
        if_stats = psutil.net_if_stats()

        for name, counters in io_counters.items():
            # Skip loopback
            if name == 'lo':
                continue

            # Check if interface is up
            is_up = False
            if name in if_stats:
                is_up = if_stats[name].isup

            # Calculate rates
            send_rate = self._rate_calc.calculate_rate(
                f"net.{name}.bytes_sent", counters.bytes_sent
            )
            recv_rate = self._rate_calc.calculate_rate(
                f"net.{name}.bytes_recv", counters.bytes_recv
            )

            interfaces.append({
                'name': name,
                'is_up': is_up,
                'bytes_sent_total': counters.bytes_sent,
                'bytes_recv_total': counters.bytes_recv,
                'packets_sent_total': counters.packets_sent,
                'packets_recv_total': counters.packets_recv,
                'errors_in': counters.errin,
                'errors_out': counters.errout,
                'drops_in': counters.dropin,
                'drops_out': counters.dropout,
                'bytes_sent_per_sec': send_rate,
                'bytes_recv_per_sec': recv_rate,
            })

        return interfaces
