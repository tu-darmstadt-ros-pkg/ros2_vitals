"""Collector for per-process TCP network statistics using ss command."""

import subprocess
import re
import time
from typing import Dict, Optional, Set

from ..utils.rate_calculator import RateCalculator


# Regex to find Process Info: users:(("process_name",pid=123,fd=4))
REGEX_PROCESS = re.compile(r'users:\(\("(?P<name>[^"]+)",pid=(?P<pid>\d+),')

# Regex to find Metrics (looks for keywords anywhere in the line)
REGEX_METRICS = re.compile(r'bytes_acked:(?P<tx>\d+).*bytes_received:(?P<rx>\d+)')


class TcpStatsCollector:
    """
    Collects per-process TCP network statistics using the 'ss' command.

    Uses 'ss -t -i -p -n' to get internal TCP counters (bytes sent/received)
    for all processes with active TCP connections.
    """

    def __init__(self):
        self._rate_calc = RateCalculator()
        # Cache: pid -> {'rx_total': int, 'tx_total': int}
        self._prev_stats: Dict[int, Dict[str, int]] = {}
        self._last_collect_time = 0.0
        # Track which PIDs we've seen for cache cleanup
        self._last_seen_pids: Set[int] = set()
        # Check if ss command is available
        self._ss_available = self._check_ss_available()

    def _check_ss_available(self) -> bool:
        """Check if the 'ss' command is available."""
        try:
            result = subprocess.run(
                ["ss", "--version"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @property
    def available(self) -> bool:
        """Whether TCP stats collection is available."""
        return self._ss_available

    def collect_stats(self) -> Dict[int, Dict[str, float]]:
        """
        Collect TCP network statistics for all processes.

        Returns:
            Dict mapping PID to {'rx_bytes_per_sec': float, 'tx_bytes_per_sec': float}
            Only includes PIDs that have active TCP connections.
        """
        if not self._ss_available:
            return {}

        # Run ss command to get TCP stats
        # -t: TCP, -i: Internal info, -p: Process info, -n: Numeric IPs
        try:
            result = subprocess.run(
                ["ss", "-t", "-i", "-p", "-n"],
                capture_output=True,
                text=True,
                timeout=5
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return {}

        if result.returncode != 0:
            return {}

        # Parse ss output
        current_stats = self._parse_ss_output(result.stdout)
        current_time = time.time()
        current_pids = set(current_stats.keys())

        # Calculate rates
        rates = {}
        for pid, stats in current_stats.items():
            rx_rate = self._rate_calc.calculate_rate(
                f"tcp.{pid}.rx", stats['rx_total']
            )
            tx_rate = self._rate_calc.calculate_rate(
                f"tcp.{pid}.tx", stats['tx_total']
            )
            rates[pid] = {
                'rx_bytes_per_sec': rx_rate,
                'tx_bytes_per_sec': tx_rate,
                'rx_total': stats['rx_total'],
                'tx_total': stats['tx_total'],
            }

        # Clean up stale cache entries
        stale_pids = self._last_seen_pids - current_pids
        for pid in stale_pids:
            self._rate_calc.remove_key(f"tcp.{pid}.rx")
            self._rate_calc.remove_key(f"tcp.{pid}.tx")

        self._last_seen_pids = current_pids
        self._prev_stats = current_stats
        self._last_collect_time = current_time

        return rates

    def _parse_ss_output(self, output: str) -> Dict[int, Dict[str, int]]:
        """
        Parse ss command output to extract per-process TCP statistics.

        Args:
            output: Raw output from 'ss -t -i -p -n'

        Returns:
            Dict mapping PID to {'rx_total': int, 'tx_total': int}
        """
        pid_stats: Dict[int, Dict[str, int]] = {}

        current_pid: Optional[int] = None

        for line in output.splitlines():
            line = line.strip()

            # Match Process Line (identifies who owns the socket)
            if "users:" in line:
                p_match = REGEX_PROCESS.search(line)
                if p_match:
                    current_pid = int(p_match.group('pid'))
                else:
                    current_pid = None

            # Match Metrics Line (contains the counters)
            elif current_pid is not None:
                m_match = REGEX_METRICS.search(line)
                if m_match:
                    tx_bytes = int(m_match.group('tx'))
                    rx_bytes = int(m_match.group('rx'))

                    if current_pid not in pid_stats:
                        pid_stats[current_pid] = {'rx_total': 0, 'tx_total': 0}

                    # Sum up all sockets belonging to this PID
                    pid_stats[current_pid]['rx_total'] += rx_bytes
                    pid_stats[current_pid]['tx_total'] += tx_bytes

        return pid_stats

    def get_process_stats(self, pid: int, child_pids: Optional[list] = None) -> Dict[str, float]:
        """
        Get TCP stats for a specific process, including its children.

        This method looks up stats from the last collect_stats() call.
        The PID matching is robust: it checks both the process PID and
        its child PIDs since the TCP socket might be owned by a child.

        Args:
            pid: The main process ID
            child_pids: Optional list of child process IDs to also check

        Returns:
            Dict with 'rx_bytes_per_sec' and 'tx_bytes_per_sec', or zeros if not found
        """
        # We need to call collect_stats() first to have data
        # This is typically done once per collection cycle by the ProcessCollector

        rx_rate = 0.0
        tx_rate = 0.0

        # Check main PID
        if pid in self._last_stats:
            rx_rate += self._last_stats.get(pid, {}).get('rx_bytes_per_sec', 0.0)
            tx_rate += self._last_stats.get(pid, {}).get('tx_bytes_per_sec', 0.0)

        # Check child PIDs (TCP socket might be owned by a child process)
        if child_pids:
            for child_pid in child_pids:
                if child_pid in self._last_stats:
                    rx_rate += self._last_stats.get(child_pid, {}).get('rx_bytes_per_sec', 0.0)
                    tx_rate += self._last_stats.get(child_pid, {}).get('tx_bytes_per_sec', 0.0)

        return {
            'rx_bytes_per_sec': rx_rate,
            'tx_bytes_per_sec': tx_rate,
        }

    def clear(self):
        """Clear all cached statistics."""
        self._prev_stats.clear()
        self._last_seen_pids.clear()
        self._rate_calc.clear()

    # Store last stats for lookup
    _last_stats: Dict[int, Dict[str, float]] = {}

    def refresh(self) -> None:
        """Refresh the TCP stats cache (call once per collection cycle)."""
        self._last_stats = self.collect_stats()
