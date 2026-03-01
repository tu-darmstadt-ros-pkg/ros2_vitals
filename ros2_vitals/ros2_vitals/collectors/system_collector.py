"""Collector for CPU, RAM, load average, and temperature."""

import os
import socket
import time
from typing import Dict, List, Tuple

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
        # Temperature: cache the sysfs file path after first probe
        self._temp_sysfs_path = None  # Direct path to temp file, e.g. /sys/class/hwmon/hwmon3/temp1_input
        self._temp_probed = False

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

        On first call, uses psutil to discover the right sensor and caches the
        sysfs file path. Subsequent calls read the file directly (~0.1ms vs ~25ms).

        Returns:
            Temperature in Celsius, or -1.0 if unavailable
        """
        # Fast path: read cached sysfs file directly
        if self._temp_probed:
            if self._temp_sysfs_path is None:
                return -1.0
            try:
                with open(self._temp_sysfs_path, 'r') as f:
                    # sysfs temp files contain millidegrees
                    return int(f.read().strip()) / 1000.0
            except (IOError, ValueError):
                return -1.0

        # First call: probe via psutil to find the right sensor
        self._temp_probed = True
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return -1.0

            # Find the right sensor entry
            sensor_name = None
            for name in ['coretemp', 'cpu_thermal', 'k10temp', 'zenpower', 'acpitz']:
                if name in temps and temps[name]:
                    sensor_name = name
                    break
            if sensor_name is None:
                # Fallback: first available
                for name, entries in temps.items():
                    if entries:
                        sensor_name = name
                        break

            if sensor_name is None:
                return -1.0

            # Find the sysfs path for this sensor
            # psutil stores it in the shwtemp named tuple's internal attributes
            # but we can find it by scanning /sys/class/hwmon/
            temp_value = temps[sensor_name][0].current
            self._temp_sysfs_path = self._find_temp_sysfs_path(sensor_name)
            return temp_value

        except (AttributeError, KeyError, Exception):
            return -1.0

    def _find_temp_sysfs_path(self, sensor_name: str) -> str:
        """Find the sysfs file path for a temperature sensor by name."""
        import glob
        hwmon_dirs = glob.glob('/sys/class/hwmon/hwmon*')
        for hwmon_dir in hwmon_dirs:
            try:
                name_file = os.path.join(hwmon_dir, 'name')
                with open(name_file, 'r') as f:
                    name = f.read().strip()
                if name == sensor_name:
                    # Return the first temp input file
                    temp_file = os.path.join(hwmon_dir, 'temp1_input')
                    if os.path.exists(temp_file):
                        return temp_file
            except (IOError, OSError):
                continue
        return None

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
        self._sub_timings = {}

        t0 = time.perf_counter()
        ram_total, ram_used, ram_available = self.get_memory()
        swap_total, swap_used = self.get_swap()
        self._sub_timings['mem+swap'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        load_1, load_5, load_15 = self.get_load_average()
        self._sub_timings['load'] = time.perf_counter() - t0

        # Single CPU measurement: per-core values, derive overall from them
        t0 = time.perf_counter()
        cpu_per_core = self.get_cpu_per_core()
        cpu_overall = sum(cpu_per_core) / len(cpu_per_core) if cpu_per_core else 0.0
        self._sub_timings['cpu'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        cpu_temp = self.get_cpu_temperature()
        self._sub_timings['temp'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        uptime = self.get_uptime()
        self._sub_timings['uptime'] = time.perf_counter() - t0

        return {
            'hostname': self.get_hostname(),
            'ip_addresses': self.get_ip_addresses(),
            'cpu_percent': cpu_overall,
            'cpu_count': self.get_cpu_count(),
            'cpu_per_core': cpu_per_core,
            'load_avg_1min': load_1,
            'load_avg_5min': load_5,
            'load_avg_15min': load_15,
            'ram_total_bytes': ram_total,
            'ram_used_bytes': ram_used,
            'ram_available_bytes': ram_available,
            'swap_total_bytes': swap_total,
            'swap_used_bytes': swap_used,
            'cpu_temperature_celsius': cpu_temp,
            'uptime_seconds': uptime,
        }
