"""Collector for disk usage and I/O statistics."""

from typing import List, Dict, Any

import psutil

from ..utils.rate_calculator import RateCalculator


class DiskCollector:
    """Collects disk partition usage and I/O rates."""

    def __init__(self):
        self._rate_calc = RateCalculator()
        self._prev_io_counters = {}

    def get_partitions(self) -> List[Dict[str, Any]]:
        """
        Get statistics for all disk partitions.

        Returns:
            List of dictionaries with partition statistics
        """
        partitions = []

        # Get I/O counters per disk
        try:
            io_counters = psutil.disk_io_counters(perdisk=True)
        except Exception:
            io_counters = {}

        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue

            # Try to find I/O counters for this partition's device
            # Device name might be like /dev/sda1, we need sda1 or sda
            device_name = part.device.split('/')[-1] if part.device else ''
            base_device = ''.join(c for c in device_name if not c.isdigit())

            read_rate = 0.0
            write_rate = 0.0

            # Try exact device name first, then base device
            for dev_name in [device_name, base_device]:
                if dev_name in io_counters:
                    counters = io_counters[dev_name]
                    read_rate = self._rate_calc.calculate_rate(
                        f"disk.{part.mountpoint}.read", counters.read_bytes
                    )
                    write_rate = self._rate_calc.calculate_rate(
                        f"disk.{part.mountpoint}.write", counters.write_bytes
                    )
                    break

            partitions.append({
                'device': part.device,
                'mount_point': part.mountpoint,
                'filesystem': part.fstype,
                'total_bytes': usage.total,
                'used_bytes': usage.used,
                'free_bytes': usage.free,
                'usage_percent': usage.percent,
                'read_bytes_per_sec': read_rate,
                'write_bytes_per_sec': write_rate,
            })

        return partitions
