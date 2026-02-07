"""Collector for CPU, RAM, load average, and temperature."""

import os
import socket
from typing import List, Tuple

import psutil


class SystemCollector:
    """Collects system-wide CPU, memory, load, and temperature metrics."""

    def __init__(self):
        # Initialize CPU percent measurement (first call returns 0)
        psutil.cpu_percent(percpu=True)
        # Cache hostname and IP addresses (rarely change)
        self._hostname = socket.gethostname()
        self._ip_addresses = None
        self._ip_cache_time = 0
        # Temperature sensor name cache (avoid re-probing)
        self._temp_sensor_name = None
        self._temp_checked = False

    def get_hostname(self) -> str:
        """Get the system hostname (cached)."""
        return self._hostname

    def get_ip_addresses(self) -> List[str]:
        """Get all non-loopback IP addresses (cached for 30 seconds)."""
        import time
        now = time.time()
        if self._ip_addresses is None or (now - self._ip_cache_time) > 30:
            addresses = []
            for iface, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    # Only IPv4 for now, skip loopback
                    if addr.family == socket.AF_INET and not addr.address.startswith('127.'):
                        addresses.append(addr.address)
            self._ip_addresses = addresses
            self._ip_cache_time = now
        return self._ip_addresses

    def get_cpu_percent(self) -> float:
        """Get overall CPU usage percentage (0-100)."""
        return psutil.cpu_percent()

    def get_cpu_count(self) -> int:
        """Get number of CPU cores."""
        return psutil.cpu_count() or 1

    def get_cpu_per_core(self) -> List[float]:
        """Get per-core CPU usage percentages."""
        return psutil.cpu_percent(percpu=True)

    def get_load_average(self) -> Tuple[float, float, float]:
        """Get 1, 5, and 15 minute load averages."""
        try:
            return os.getloadavg()
        except (OSError, AttributeError):
            # Not available on some platforms
            return (0.0, 0.0, 0.0)

    def get_memory(self) -> Tuple[int, int, int]:
        """
        Get memory statistics.

        Returns:
            Tuple of (total_bytes, used_bytes, available_bytes)
        """
        mem = psutil.virtual_memory()
        return (mem.total, mem.used, mem.available)

    def get_swap(self) -> Tuple[int, int]:
        """
        Get swap statistics.

        Returns:
            Tuple of (total_bytes, used_bytes)
        """
        swap = psutil.swap_memory()
        return (swap.total, swap.used)

    def get_cpu_temperature(self) -> float:
        """
        Get CPU temperature in Celsius.

        Returns:
            Temperature in Celsius, or -1.0 if unavailable
        """
        try:
            # Use cached sensor name if already found
            if self._temp_checked and self._temp_sensor_name is None:
                return -1.0

            temps = psutil.sensors_temperatures()
            if not temps:
                self._temp_checked = True
                self._temp_sensor_name = None
                return -1.0

            # If we already know the sensor name, use it directly
            if self._temp_sensor_name and self._temp_sensor_name in temps:
                entries = temps[self._temp_sensor_name]
                if entries:
                    return entries[0].current

            # Try common sensor names
            for name in ['coretemp', 'cpu_thermal', 'k10temp', 'zenpower', 'acpitz']:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        self._temp_sensor_name = name
                        self._temp_checked = True
                        return entries[0].current

            # Fallback: return first available temperature
            for name, entries in temps.items():
                if entries:
                    self._temp_sensor_name = name
                    self._temp_checked = True
                    return entries[0].current

            self._temp_checked = True
            self._temp_sensor_name = None
            return -1.0
        except (AttributeError, KeyError):
            self._temp_checked = True
            self._temp_sensor_name = None
            return -1.0

    def get_uptime(self) -> float:
        """Get system uptime in seconds."""
        import time
        return time.time() - psutil.boot_time()

    def collect_all(self) -> dict:
        """
        Collect all system metrics.

        Returns:
            Dictionary with all system metrics
        """
        ram_total, ram_used, ram_available = self.get_memory()
        swap_total, swap_used = self.get_swap()
        load_1, load_5, load_15 = self.get_load_average()

        return {
            'hostname': self.get_hostname(),
            'ip_addresses': self.get_ip_addresses(),
            'cpu_percent': self.get_cpu_percent(),
            'cpu_count': self.get_cpu_count(),
            'cpu_per_core': self.get_cpu_per_core(),
            'load_avg_1min': load_1,
            'load_avg_5min': load_5,
            'load_avg_15min': load_15,
            'ram_total_bytes': ram_total,
            'ram_used_bytes': ram_used,
            'ram_available_bytes': ram_available,
            'swap_total_bytes': swap_total,
            'swap_used_bytes': swap_used,
            'cpu_temperature_celsius': self.get_cpu_temperature(),
            'uptime_seconds': self.get_uptime(),
        }
