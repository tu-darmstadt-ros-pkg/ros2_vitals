"""Per-process network statistics collector with eBPF/ss backend selection.

Tries eBPF first (TCP + UDP, ~0.1ms per read, requires root).
Falls back to the ``ss`` subprocess (TCP only, ~100ms per read).
"""

import re
import subprocess
import time
from typing import Dict, Optional, Set

from ..utils.rate_calculator import RateCalculator
from .ebpf_net_collector import EbpfNetCollector

# Regex for ss output parsing (fallback path)
_REGEX_PROCESS = re.compile(r'users:\(\("(?P<name>[^"]+)",pid=(?P<pid>\d+),')
_REGEX_METRICS = re.compile(r'bytes_acked:(?P<tx>\d+).*bytes_received:(?P<rx>\d+)')


class NetStatsCollector:
    """Per-process network byte-rate collector.

    Uses eBPF kprobes if available (captures TCP + UDP).
    Falls back to the ``ss`` command (TCP only).
    """

    def __init__(self):
        self._rate_calc = RateCalculator()
        self._last_collect_time = 0.0
        self._last_seen_pids: Set[int] = set()
        self._backend = 'none'

        # Try eBPF first
        self._ebpf = EbpfNetCollector()
        if self._ebpf.available:
            self._backend = 'ebpf'
        else:
            # Fall back to ss
            if self._check_ss_available():
                self._backend = 'ss'

    # ------------------------------------------------------------------
    # Public interface (same contract as old TcpStatsCollector)
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether any backend is available."""
        return self._backend != 'none'

    @property
    def backend_name(self) -> str:
        """Return 'ebpf', 'ss', or 'none'."""
        return self._backend

    # Store last stats for lookup by ProcessCollector
    _last_stats: Dict[int, Dict[str, float]] = {}

    def refresh(self) -> None:
        """Refresh stats cache (call once per collection cycle)."""
        if self._backend == 'ebpf':
            self._refresh_ebpf()
        elif self._backend == 'ss':
            self._refresh_ss()

    def get_process_stats(self, pid: int,
                          child_pids: Optional[list] = None) -> Dict[str, float]:
        """Get aggregated network stats for a process and its children."""
        rx_rate = 0.0
        tx_rate = 0.0

        if pid in self._last_stats:
            rx_rate += self._last_stats[pid].get('rx_bytes_per_sec', 0.0)
            tx_rate += self._last_stats[pid].get('tx_bytes_per_sec', 0.0)

        if child_pids:
            for child_pid in child_pids:
                if child_pid in self._last_stats:
                    rx_rate += self._last_stats[child_pid].get('rx_bytes_per_sec', 0.0)
                    tx_rate += self._last_stats[child_pid].get('tx_bytes_per_sec', 0.0)

        return {
            'rx_bytes_per_sec': rx_rate,
            'tx_bytes_per_sec': tx_rate,
        }

    def clear(self):
        """Clear all cached statistics."""
        self._last_seen_pids.clear()
        self._rate_calc.clear()
        self._last_stats.clear()

    def shutdown(self):
        """Clean up resources (eBPF probes)."""
        if self._ebpf:
            self._ebpf.shutdown()

    # ------------------------------------------------------------------
    # eBPF backend
    # ------------------------------------------------------------------

    def _refresh_ebpf(self):
        """Read eBPF map, convert byte counts to rates."""
        raw = self._ebpf.collect_stats()
        now = time.time()
        dt = now - self._last_collect_time if self._last_collect_time > 0 else 1.0
        self._last_collect_time = now

        rates: Dict[int, Dict[str, float]] = {}
        for pid, counters in raw.items():
            total_tx = counters.get('tcp_tx', 0) + counters.get('udp_tx', 0)
            total_rx = counters.get('tcp_rx', 0) + counters.get('udp_rx', 0)
            rates[pid] = {
                'rx_bytes_per_sec': total_rx / dt,
                'tx_bytes_per_sec': total_tx / dt,
            }

        self._last_seen_pids = set(rates.keys())
        self._last_stats = rates

    # ------------------------------------------------------------------
    # ss subprocess backend (fallback, TCP only)
    # ------------------------------------------------------------------

    @staticmethod
    def _check_ss_available() -> bool:
        """Check if the 'ss' command is available."""
        try:
            result = subprocess.run(
                ["ss", "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _refresh_ss(self):
        """Collect TCP stats via ss subprocess."""
        try:
            result = subprocess.run(
                ["ss", "-t", "-i", "-p", "-n"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return

        if result.returncode != 0:
            return

        current_stats = self._parse_ss_output(result.stdout)
        current_pids = set(current_stats.keys())

        rates: Dict[int, Dict[str, float]] = {}
        for pid, stats in current_stats.items():
            rx_rate = self._rate_calc.calculate_rate(
                f"net.{pid}.rx", stats['rx_total'],
            )
            tx_rate = self._rate_calc.calculate_rate(
                f"net.{pid}.tx", stats['tx_total'],
            )
            rates[pid] = {
                'rx_bytes_per_sec': rx_rate,
                'tx_bytes_per_sec': tx_rate,
            }

        # Clean stale rate entries
        stale_pids = self._last_seen_pids - current_pids
        for pid in stale_pids:
            self._rate_calc.remove_key(f"net.{pid}.rx")
            self._rate_calc.remove_key(f"net.{pid}.tx")

        self._last_seen_pids = current_pids
        self._last_collect_time = time.time()
        self._last_stats = rates

    @staticmethod
    def _parse_ss_output(output: str) -> Dict[int, Dict[str, int]]:
        """Parse ss output to extract per-PID TCP byte totals."""
        pid_stats: Dict[int, Dict[str, int]] = {}
        current_pid: Optional[int] = None

        for line in output.splitlines():
            line = line.strip()

            if "users:" in line:
                p_match = _REGEX_PROCESS.search(line)
                current_pid = int(p_match.group('pid')) if p_match else None

            elif current_pid is not None:
                m_match = _REGEX_METRICS.search(line)
                if m_match:
                    tx_bytes = int(m_match.group('tx'))
                    rx_bytes = int(m_match.group('rx'))

                    if current_pid not in pid_stats:
                        pid_stats[current_pid] = {'rx_total': 0, 'tx_total': 0}

                    pid_stats[current_pid]['rx_total'] += rx_bytes
                    pid_stats[current_pid]['tx_total'] += tx_bytes

        return pid_stats
