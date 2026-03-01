"""Collector for ROS process discovery and statistics."""

import os
import re
import time
from typing import List, Dict, Any, Optional, Set
import logging

import psutil

from ..utils.rate_calculator import RateCalculator
from .gpu_collector import GpuCollector
from .tcp_stats_collector import TcpStatsCollector

logger = logging.getLogger(__name__)

# Patterns to identify ROS 2 related processes
ROS_CMDLINE_PATTERNS = [
    r'ros2\s+run',
    r'ros2\s+launch',
    r'component_container',
    r'/opt/ros/',
    r'python3.*ros2',
    r'_ros2_',
    r'--ros-args',
]

# Compiled patterns for efficiency
_ROS_PATTERNS = [re.compile(p) for p in ROS_CMDLINE_PATTERNS]


class ProcessCollector:
    """
    Discovers and collects statistics for ROS 2 processes.

    Uses /proc scanning to find processes without requiring node registration.
    Aggregates statistics from child processes.
    """

    def __init__(self, gpu_collector: Optional[GpuCollector] = None,
                 tcp_stats_collector: Optional[TcpStatsCollector] = None):
        self._rate_calc = RateCalculator()
        self._gpu_collector = gpu_collector
        self._tcp_stats_collector = tcp_stats_collector
        # Cache Process objects to enable proper cpu_percent() tracking
        # cpu_percent() needs to be called on the same Process object over time
        self._process_cache: Dict[int, psutil.Process] = {}
        # Cache last CPU values for processes (needed for proper CPU measurement)
        self._cpu_cache: Dict[int, float] = {}
        # Track which PIDs we've seen to clean up stale cache entries
        self._last_seen_pids: Set[int] = set()
        # Cache container names (rarely changes)
        self._container_cache: Dict[int, str] = {}
        # Cache node names and namespaces (never change for a running process)
        self._node_name_cache: Dict[int, str] = {}
        self._namespace_cache: Dict[int, str] = {}
        # Cache GPU process memory (updated once per cycle, not per process)
        self._gpu_process_cache: Dict[int, Dict[str, Any]] = {}
        # Cache TCP stats (updated once per cycle)
        self._tcp_stats_cache: Dict[int, Dict[str, float]] = {}
        # Cache ROS process identification: pid -> True (is ROS) or False (not ROS)
        # Avoids re-reading /proc/<pid>/cmdline for known non-ROS processes
        self._ros_pid_cache: Dict[int, bool] = {}
        # Full discovery interval: do a full cmdline scan every N cycles
        self._discovery_cycle = 0
        self._discovery_interval = 5  # Full scan every 5 seconds at 1Hz

    def get_processes(self, include_children: bool = True) -> List[Dict[str, Any]]:
        """
        Discover and collect statistics for all ROS processes.

        Args:
            include_children: Whether to aggregate child process statistics

        Returns:
            List of dictionaries with process statistics
        """
        ros_processes = []
        seen_pids: Set[int] = set()
        current_pids: Set[int] = set()
        self._sub_timings = {}

        # Build GPU process cache once per cycle (expensive NVML call)
        t0 = time.perf_counter()
        self._refresh_gpu_process_cache()
        self._sub_timings['gpu_cache'] = time.perf_counter() - t0

        # Refresh TCP stats cache once per cycle (expensive ss command)
        t0 = time.perf_counter()
        self._refresh_tcp_stats_cache()
        self._sub_timings['tcp_ss'] = time.perf_counter() - t0

        # Decide if this is a full discovery cycle or a fast stats-only cycle.
        # Full discovery reads cmdline for all processes (expensive).
        # Fast cycles only scan pid+ppid and use cached ROS PID set.
        self._discovery_cycle += 1
        full_discovery = (self._discovery_cycle % self._discovery_interval == 1
                          or not self._ros_pid_cache)

        # Scan processes: full discovery reads cmdline, fast cycle skips it
        t0 = time.perf_counter()
        if full_discovery:
            all_procs = list(psutil.process_iter(['pid', 'ppid', 'name', 'cmdline']))
        else:
            all_procs = list(psutil.process_iter(['pid', 'ppid', 'name']))

        # Build parent-child map
        children_by_ppid: Dict[int, List[psutil.Process]] = {}
        for proc in all_procs:
            ppid = proc.info.get('ppid')
            if ppid is not None:
                children_by_ppid.setdefault(ppid, []).append(proc)
        self._sub_timings['proc_iter'] = time.perf_counter() - t0

        # Find all ROS-related processes
        t0 = time.perf_counter()
        if full_discovery:
            # Full discovery: check every process, update the ROS PID cache
            new_ros_cache: Dict[int, bool] = {}
            for proc in all_procs:
                try:
                    pid = proc.info['pid']

                    if pid in seen_pids:
                        continue

                    cmdline = proc.info['cmdline']
                    if not cmdline:
                        new_ros_cache[pid] = False
                        continue

                    cmdline_str = ' '.join(cmdline)

                    if self._is_shell_wrapper(cmdline):
                        new_ros_cache[pid] = False
                        continue

                    if not self._is_ros_process(cmdline_str):
                        new_ros_cache[pid] = False
                        continue

                    new_ros_cache[pid] = True

                    # Use cached Process object for proper CPU tracking
                    if pid in self._process_cache:
                        cached_proc = self._process_cache[pid]
                        try:
                            if cached_proc.create_time() == proc.create_time():
                                proc = cached_proc
                            else:
                                self._process_cache[pid] = proc
                                self._cpu_cache.pop(pid, None)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            self._process_cache[pid] = proc
                            self._cpu_cache.pop(pid, None)
                    else:
                        self._process_cache[pid] = proc

                    current_pids.add(pid)

                    proc_info = self._collect_process_stats(proc, include_children, children_by_ppid)
                    if proc_info:
                        ros_processes.append(proc_info)
                        if include_children:
                            for child_pid in proc_info.get('child_pids', []):
                                seen_pids.add(child_pid)
                                current_pids.add(child_pid)

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

            self._ros_pid_cache = new_ros_cache
        else:
            # Fast cycle: only process known ROS PIDs, skip cmdline reads
            # Build a pid -> proc lookup for fast access
            proc_by_pid = {p.info['pid']: p for p in all_procs}

            for pid, is_ros in self._ros_pid_cache.items():
                if not is_ros:
                    continue

                if pid in seen_pids:
                    continue

                proc = proc_by_pid.get(pid)
                if proc is None:
                    continue

                # Use cached Process object for proper CPU tracking
                if pid in self._process_cache:
                    cached_proc = self._process_cache[pid]
                    try:
                        if cached_proc.create_time() == proc.create_time():
                            proc = cached_proc
                        else:
                            self._process_cache[pid] = proc
                            self._cpu_cache.pop(pid, None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        self._process_cache[pid] = proc
                        self._cpu_cache.pop(pid, None)
                else:
                    self._process_cache[pid] = proc

                current_pids.add(pid)

                try:
                    proc_info = self._collect_process_stats(proc, include_children, children_by_ppid)
                    if proc_info:
                        ros_processes.append(proc_info)
                        if include_children:
                            for child_pid in proc_info.get('child_pids', []):
                                seen_pids.add(child_pid)
                                current_pids.add(child_pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

        self._sub_timings['ros_stats'] = time.perf_counter() - t0
        self._sub_timings['ros_count'] = len(ros_processes)
        self._sub_timings['full_scan'] = full_discovery

        # Clean up stale cache entries (processes that no longer exist)
        stale_pids = self._last_seen_pids - current_pids
        for pid in stale_pids:
            self._process_cache.pop(pid, None)
            self._cpu_cache.pop(pid, None)
            self._rate_calc.remove_key(f"proc.{pid}.read")
            self._rate_calc.remove_key(f"proc.{pid}.write")

        self._last_seen_pids = current_pids

        # Clean up stale container/name/namespace cache entries
        for pid in stale_pids:
            self._container_cache.pop(pid, None)
            self._node_name_cache.pop(pid, None)
            self._namespace_cache.pop(pid, None)

        return ros_processes

    def _refresh_gpu_process_cache(self):
        """Refresh GPU process memory cache (called once per collection cycle)."""
        self._gpu_process_cache.clear()
        if not self._gpu_collector or not self._gpu_collector.available:
            return

        # Import here to avoid issues if pynvml not available
        try:
            from pynvml import (nvmlDeviceGetHandleByIndex,
                                nvmlDeviceGetComputeRunningProcesses,
                                nvmlDeviceGetGraphicsRunningProcesses)
        except ImportError:
            return

        for i in range(self._gpu_collector._device_count):
            try:
                handle = nvmlDeviceGetHandleByIndex(i)
                # Query both compute and graphics processes
                for get_procs in (nvmlDeviceGetComputeRunningProcesses,
                                  nvmlDeviceGetGraphicsRunningProcesses):
                    try:
                        processes = get_procs(handle)
                        for proc in processes:
                            pid = proc.pid
                            mem = proc.usedGpuMemory or 0
                            if pid in self._gpu_process_cache:
                                # Same PID can appear in both lists; take the max
                                self._gpu_process_cache[pid]['memory_bytes'] = max(
                                    self._gpu_process_cache[pid]['memory_bytes'], mem)
                            else:
                                self._gpu_process_cache[pid] = {
                                    'gpu_index': i,
                                    'memory_bytes': mem,
                                }
                    except Exception:
                        continue
            except Exception:
                continue

    def _get_process_gpu_memory(self, pid: int) -> Optional[Dict[str, Any]]:
        """Get GPU memory from cache (O(1) lookup instead of O(n*m))."""
        return self._gpu_process_cache.get(pid)

    def _refresh_tcp_stats_cache(self):
        """Refresh TCP stats cache (called once per collection cycle)."""
        self._tcp_stats_cache.clear()
        if not self._tcp_stats_collector or not self._tcp_stats_collector.available:
            return
        self._tcp_stats_collector.refresh()
        self._tcp_stats_cache = self._tcp_stats_collector._last_stats

    def _get_process_tcp_stats(self, pid: int, child_pids: Optional[List[int]] = None) -> Dict[str, float]:
        """
        Get TCP stats for a process, aggregating stats from child processes.

        The ss command may report the socket as owned by either the parent or
        a child process, so we check all PIDs in the process tree.
        """
        rx_rate = 0.0
        tx_rate = 0.0

        # Check main PID
        if pid in self._tcp_stats_cache:
            stats = self._tcp_stats_cache[pid]
            rx_rate += stats.get('rx_bytes_per_sec', 0.0)
            tx_rate += stats.get('tx_bytes_per_sec', 0.0)

        # Check child PIDs (TCP socket might be owned by a child process)
        if child_pids:
            for child_pid in child_pids:
                if child_pid in self._tcp_stats_cache:
                    stats = self._tcp_stats_cache[child_pid]
                    rx_rate += stats.get('rx_bytes_per_sec', 0.0)
                    tx_rate += stats.get('tx_bytes_per_sec', 0.0)

        return {
            'rx_bytes_per_sec': rx_rate,
            'tx_bytes_per_sec': tx_rate,
        }

    def _is_launch_process(self, cmdline_str: str) -> bool:
        """Check if this is a ros2 launch process."""
        return bool(re.search(r'ros2\s+launch', cmdline_str))

    def _get_launch_name(self, cmdline_str: str) -> str:
        """Extract launch file name from ros2 launch command."""
        match = re.search(r'ros2\s+launch\s+(?:--namespace\s+\S+\s+)?(\S+)\s+(\S+)', cmdline_str)
        if match:
            package = match.group(1)
            launch_file = match.group(2)
            launch_name = re.sub(r'\.launch\.(py|yaml|xml)$', '', launch_file)
            return f"{package}/{launch_name}"
        return ""

    def _is_ros_process(self, cmdline: str) -> bool:
        """Check if command line indicates a ROS 2 process."""
        for pattern in _ROS_PATTERNS:
            if pattern.search(cmdline):
                return True
        return False

    def _is_shell_wrapper(self, cmdline: List[str]) -> bool:
        """Check if this is a shell wrapper process (e.g., /bin/sh -c ...)."""
        if len(cmdline) < 2:
            return False
        shell_names = {'sh', 'bash', 'dash'}
        if os.path.basename(cmdline[0]) not in shell_names:
            return False
        return '-c' in cmdline

    def _collect_process_stats(
        self, proc: psutil.Process, include_children: bool,
        children_by_ppid: Optional[Dict[int, List[psutil.Process]]] = None
    ) -> Optional[Dict[str, Any]]:
        """Collect statistics for a single process."""
        try:
            pid = proc.pid
            cmdline_list = proc.cmdline()
            cmdline = ' '.join(cmdline_list)

            # Check if this is a launch process
            is_launch = self._is_launch_process(cmdline)
            launch_name = self._get_launch_name(cmdline) if is_launch else ""

            # Basic info - use cached values (container, node name, namespace)
            if pid not in self._container_cache:
                self._container_cache[pid] = self._get_container_name(pid)
            if pid not in self._node_name_cache:
                self._node_name_cache[pid] = self._extract_node_name_from_cmdline(cmdline_list, cmdline, proc)
            if pid not in self._namespace_cache:
                self._namespace_cache[pid] = self._extract_namespace_from_cmdline(cmdline, proc)

            info = {
                'pid': pid,
                'cmdline': cmdline[:500],  # Limit length
                'node_name': self._node_name_cache[pid],
                'node_namespace': self._namespace_cache[pid],
                'container_name': self._container_cache[pid],
                'child_pids': [],
                'is_launch_process': is_launch,
                'launch_name': launch_name,
                'child_nodes': [],  # Will be populated for launch processes
            }

            # Status
            info['status'] = proc.status()
            info['num_threads'] = proc.num_threads()
            info['create_time'] = proc.create_time()

            # CPU - use cpu_percent() which returns delta since last call
            # The Process object is cached, so subsequent calls give accurate readings
            try:
                cpu = proc.cpu_percent()
                # Store in cache for reference
                self._cpu_cache[pid] = cpu
                info['cpu_percent'] = cpu
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                info['cpu_percent'] = self._cpu_cache.get(pid, 0.0)

            # Memory
            try:
                mem = proc.memory_info()
                info['ram_bytes_self'] = mem.rss
                info['ram_bytes'] = mem.rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                info['ram_bytes_self'] = 0
                info['ram_bytes'] = 0

            # Disk I/O
            try:
                io = proc.io_counters()
                info['disk_read_bytes_total'] = io.read_bytes
                info['disk_write_bytes_total'] = io.write_bytes

                # Calculate rates
                info['disk_read_bytes_per_sec'] = self._rate_calc.calculate_rate(
                    f"proc.{pid}.read", io.read_bytes
                )
                info['disk_write_bytes_per_sec'] = self._rate_calc.calculate_rate(
                    f"proc.{pid}.write", io.write_bytes
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                info['disk_read_bytes_total'] = 0
                info['disk_write_bytes_total'] = 0
                info['disk_read_bytes_per_sec'] = 0.0
                info['disk_write_bytes_per_sec'] = 0.0

            # Skip expensive open_files() and connections() calls
            # These are rarely needed and cause significant CPU overhead
            info['open_files_count'] = 0
            info['network_connections_count'] = 0

            # GPU memory - use cached lookup (O(1) instead of O(n*m))
            info['gpu_index'] = -1
            info['gpu_memory_bytes'] = 0
            gpu_info = self._get_process_gpu_memory(pid)
            if gpu_info:
                info['gpu_index'] = gpu_info['gpu_index']
                info['gpu_memory_bytes'] = gpu_info['memory_bytes']

            # Network I/O - will be updated after children aggregation
            info['net_rx_bytes_per_sec'] = 0.0
            info['net_tx_bytes_per_sec'] = 0.0

            # Aggregate children stats
            if include_children:
                self._aggregate_children(proc, info, children_by_ppid)

            # TCP stats - get after children are known so we can aggregate all PIDs
            tcp_stats = self._get_process_tcp_stats(pid, info.get('child_pids', []))
            info['net_rx_bytes_per_sec'] = tcp_stats['rx_bytes_per_sec']
            info['net_tx_bytes_per_sec'] = tcp_stats['tx_bytes_per_sec']

            return info

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
            logger.debug(f"Failed to collect stats for process: {e}")
            return None

    def _get_descendants(self, pid: int,
                         children_by_ppid: Dict[int, List[psutil.Process]]) -> List[psutil.Process]:
        """Get all descendants of a PID using pre-built ppid map."""
        result = []
        direct_children = children_by_ppid.get(pid, [])
        for child in direct_children:
            result.append(child)
            result.extend(self._get_descendants(child.pid, children_by_ppid))
        return result

    def _aggregate_children(self, proc: psutil.Process, info: Dict[str, Any],
                            children_by_ppid: Optional[Dict[int, List[psutil.Process]]] = None):
        """Aggregate statistics from child processes."""
        try:
            if children_by_ppid is not None:
                children = self._get_descendants(proc.pid, children_by_ppid)
            else:
                children = proc.children(recursive=True)
            child_pids = []
            child_nodes = []

            for child in children:
                try:
                    child_pid = child.pid
                    child_pids.append(child_pid)

                    # Use cached Process object for accurate CPU readings
                    if child_pid in self._process_cache:
                        cached_child = self._process_cache[child_pid]
                        try:
                            if cached_child.create_time() == child.create_time():
                                child = cached_child
                            else:
                                self._process_cache[child_pid] = child
                                self._cpu_cache.pop(child_pid, None)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            self._process_cache[child_pid] = child
                            self._cpu_cache.pop(child_pid, None)
                    else:
                        self._process_cache[child_pid] = child

                    # Collect individual stats for this child
                    try:
                        child_cpu = child.cpu_percent()
                        self._cpu_cache[child_pid] = child_cpu
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        child_cpu = self._cpu_cache.get(child_pid, 0.0)

                    try:
                        child_mem = child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        child_mem = 0

                    # CPU
                    info['cpu_percent'] += child_cpu

                    # RAM
                    info['ram_bytes'] += child_mem

                    # Skip per-child disk I/O - too expensive
                    # Parent disk I/O already gives aggregate view
                    child_disk_read_rate = 0.0
                    child_disk_write_rate = 0.0

                    # GPU memory - use cached lookup
                    child_gpu_index = -1
                    child_gpu_mem = 0
                    gpu_info = self._get_process_gpu_memory(child_pid)
                    if gpu_info:
                        info['gpu_memory_bytes'] += gpu_info['memory_bytes']
                        child_gpu_index = gpu_info['gpu_index']
                        child_gpu_mem = gpu_info['memory_bytes']
                        # Use first GPU found if parent doesn't have one
                        if info['gpu_index'] == -1:
                            info['gpu_index'] = gpu_info['gpu_index']

                    # For launch processes, collect individual child node info
                    if info.get('is_launch_process', False):
                        try:
                            child_cmdline_list = child.cmdline()
                            child_cmdline = ' '.join(child_cmdline_list)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue

                        # Skip if this child is itself a launch process
                        if not self._is_launch_process(child_cmdline):
                            # Use cached node name/namespace
                            if child_pid not in self._node_name_cache:
                                self._node_name_cache[child_pid] = self._extract_node_name_from_cmdline(child_cmdline_list, child_cmdline)
                            node_name = self._node_name_cache[child_pid]
                            # Only add if we can extract a meaningful node name
                            if node_name:
                                if child_pid not in self._namespace_cache:
                                    self._namespace_cache[child_pid] = self._extract_namespace_from_cmdline(child_cmdline)
                                # Get TCP stats for this child
                                child_tcp_stats = self._get_process_tcp_stats(child_pid)
                                child_node_info = {
                                    'pid': child_pid,
                                    'cmdline': child_cmdline[:500],
                                    'node_name': node_name,
                                    'node_namespace': self._namespace_cache[child_pid],
                                    'container_name': '',
                                    'child_pids': [],
                                    'is_launch_process': False,
                                    'launch_name': '',
                                    'child_nodes': [],
                                    'status': child.status(),
                                    'num_threads': child.num_threads(),
                                    'create_time': child.create_time(),
                                    'cpu_percent': child_cpu,
                                    'ram_bytes': child_mem,
                                    'ram_bytes_self': child_mem,
                                    'disk_read_bytes_total': 0,
                                    'disk_write_bytes_total': 0,
                                    'disk_read_bytes_per_sec': child_disk_read_rate,
                                    'disk_write_bytes_per_sec': child_disk_write_rate,
                                    'net_rx_bytes_per_sec': child_tcp_stats['rx_bytes_per_sec'],
                                    'net_tx_bytes_per_sec': child_tcp_stats['tx_bytes_per_sec'],
                                    'open_files_count': 0,
                                    'network_connections_count': 0,
                                    'gpu_index': child_gpu_index,
                                    'gpu_memory_bytes': child_gpu_mem,
                                }
                                child_nodes.append(child_node_info)

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

            info['child_pids'] = child_pids
            info['child_nodes'] = child_nodes

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    def _extract_node_name_from_cmdline(self, cmdline: List[str], cmdline_str: str,
                                       proc: Optional[psutil.Process] = None) -> str:
        """Extract ROS node name from pre-fetched cmdline (avoids extra proc.cmdline() call)."""
        # Look for --ros-args -r __node:=<name>
        for i, arg in enumerate(cmdline):
            if arg == '__node:=' or arg.startswith('__node:='):
                if '=' in arg:
                    return arg.split('=', 1)[1]
                elif i + 1 < len(cmdline):
                    return cmdline[i + 1]

        # Look for -r __node:=<name>
        match = re.search(r'__node:=(\S+)', cmdline_str)
        if match:
            return match.group(1)

        # Try environment variable (expensive, but only called once due to caching)
        if proc is not None:
            try:
                environ = proc.environ()
                if 'ROS_NODE_NAME' in environ:
                    return environ['ROS_NODE_NAME']
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

        # Parse ros2 run command: ros2 run <package> <executable>
        run_match = re.search(r'ros2\s+run\s+(\S+)\s+(\S+)', cmdline_str)
        if run_match:
            return f"{run_match.group(1)}/{run_match.group(2)}"

        # Parse ros2 launch command
        launch_match = re.search(r'ros2\s+launch\s+(?:--namespace\s+\S+\s+)?(\S+)\s+(\S+)', cmdline_str)
        if launch_match:
            launch_file = launch_match.group(2)
            launch_name = re.sub(r'\.launch\.(py|yaml|xml)$', '', launch_file)
            return f"[launch] {launch_match.group(1)}/{launch_name}"

        # Handle ros2 CLI tools (ros2 topic, ros2 service, etc.)
        cli_name = self._extract_ros2_cli_name(cmdline)
        if cli_name:
            return cli_name

        # Handle Python inline commands (python3 -c "from ... import main; main()")
        python_inline_name = self._extract_python_inline_name(cmdline)
        if python_inline_name:
            return python_inline_name

        # Handle Gazebo (gz sim) processes
        gz_name = self._extract_gazebo_name(cmdline_str)
        if gz_name:
            return gz_name

        # Handle shell wrapper processes (/bin/sh -c ...)
        shell_name = self._extract_shell_command_name(cmdline)
        if shell_name:
            return shell_name

        # Check for common ROS 2 executables
        for arg in cmdline:
            # Match paths like /opt/ros/jazzy/lib/<package>/<executable>
            path_match = re.match(r'.*/lib/([^/]+)/([^/]+)$', arg)
            if path_match:
                package = path_match.group(1)
                executable = path_match.group(2)
                return f"{package}/{executable}"

            # Match paths like .../install/<package>/lib/<package>/<executable>
            install_match = re.match(r'.*/install/([^/]+)/lib/\1/([^/]+)$', arg)
            if install_match:
                package = install_match.group(1)
                executable = install_match.group(2)
                return f"{package}/{executable}"

        # Last resort: try to get a meaningful name from the command
        skip_names = {'python3', 'python', 'bash', 'sh', 'ruby', 'node'}
        for arg in cmdline:
            basename = os.path.basename(arg)
            if basename not in skip_names and not basename.startswith('-'):
                if '/' in arg or basename.endswith('.py'):
                    name = os.path.splitext(basename)[0]
                    if name and name not in skip_names:
                        return name
                elif basename:
                    return basename

        # Final fallback: use executable name
        if cmdline:
            basename = os.path.basename(cmdline[0])
            if basename not in {'python3', 'python', 'bash', 'sh'}:
                return basename

        return ''

    def _extract_namespace_from_cmdline(self, cmdline_str: str,
                                       proc: Optional[psutil.Process] = None) -> str:
        """Extract ROS namespace from pre-fetched cmdline string."""
        # Look for __ns:=<namespace>
        match = re.search(r'__ns:=(\S+)', cmdline_str)
        if match:
            return match.group(1)

        # Look for --namespace <ns> in launch command
        match = re.search(r'--namespace\s+(\S+)', cmdline_str)
        if match:
            return match.group(1)

        # Try environment variable (expensive, but only called once due to caching)
        if proc is not None:
            try:
                environ = proc.environ()
                if 'ROS_NAMESPACE' in environ:
                    return environ['ROS_NAMESPACE']
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

        return ''

    def _extract_node_name(self, proc: psutil.Process) -> str:
        """Try to extract ROS node name from process info."""
        try:
            cmdline = proc.cmdline()
            cmdline_str = ' '.join(cmdline)

            # Look for --ros-args -r __node:=<name>
            for i, arg in enumerate(cmdline):
                if arg == '__node:=' or arg.startswith('__node:='):
                    if '=' in arg:
                        return arg.split('=', 1)[1]
                    elif i + 1 < len(cmdline):
                        return cmdline[i + 1]

            # Look for -r __node:=<name>
            match = re.search(r'__node:=(\S+)', cmdline_str)
            if match:
                return match.group(1)

            # Try environment variable
            try:
                environ = proc.environ()
                if 'ROS_NODE_NAME' in environ:
                    return environ['ROS_NODE_NAME']
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

            # Parse ros2 run command: ros2 run <package> <executable> [args]
            run_match = re.search(r'ros2\s+run\s+(\S+)\s+(\S+)', cmdline_str)
            if run_match:
                package = run_match.group(1)
                executable = run_match.group(2)
                return f"{package}/{executable}"

            # Parse ros2 launch command: ros2 launch [--namespace <ns>] <package> <launch_file> [args]
            # Handle optional --namespace argument
            launch_match = re.search(r'ros2\s+launch\s+(?:--namespace\s+\S+\s+)?(\S+)\s+(\S+)', cmdline_str)
            if launch_match:
                package = launch_match.group(1)
                launch_file = launch_match.group(2)
                # Remove .launch.py, .launch.yaml, .launch.xml extensions
                launch_name = re.sub(r'\.launch\.(py|yaml|xml)$', '', launch_file)
                return f"[launch] {package}/{launch_name}"

            # Handle ros2 CLI tools (ros2 topic, ros2 service, etc.)
            cli_name = self._extract_ros2_cli_name(cmdline)
            if cli_name:
                return cli_name

            # Handle Python inline commands (python3 -c "from ... import main; main()")
            python_inline_name = self._extract_python_inline_name(cmdline)
            if python_inline_name:
                return python_inline_name

            # Handle Gazebo (gz sim) processes
            gz_name = self._extract_gazebo_name(cmdline_str)
            if gz_name:
                return gz_name

            # Handle shell wrapper processes (/bin/sh -c ...)
            shell_name = self._extract_shell_command_name(cmdline)
            if shell_name:
                return shell_name

            # Check for common ROS 2 executables (child processes of launch files)
            # Look for executable name in /opt/ros/ or install paths
            for i, arg in enumerate(cmdline):
                # Match paths like /opt/ros/jazzy/lib/<package>/<executable>
                path_match = re.match(r'.*/lib/([^/]+)/([^/]+)$', arg)
                if path_match:
                    package = path_match.group(1)
                    executable = path_match.group(2)
                    return f"{package}/{executable}"

                # Match paths like .../install/<package>/lib/<package>/<executable>
                install_match = re.match(r'.*/install/([^/]+)/lib/\1/([^/]+)$', arg)
                if install_match:
                    package = install_match.group(1)
                    executable = install_match.group(2)
                    return f"{package}/{executable}"

            # Last resort: try to get a meaningful name from the command
            # Skip common interpreter names
            skip_names = {'python3', 'python', 'bash', 'sh', 'ruby', 'node'}
            for arg in cmdline:
                basename = os.path.basename(arg)
                if basename not in skip_names and not basename.startswith('-'):
                    # If it's a path, extract the last component
                    if '/' in arg or basename.endswith('.py'):
                        name = os.path.splitext(basename)[0]
                        if name and name not in skip_names:
                            return name
                    elif basename:
                        return basename

            # Final fallback: use executable name
            return os.path.basename(cmdline[0]) if cmdline else ''

        except (psutil.NoSuchProcess, psutil.AccessDenied, IndexError):
            return ''

    def _extract_gazebo_name(self, cmdline_str: str) -> str:
        """
        Extract a meaningful name for Gazebo (gz sim) processes.

        Returns names like:
        - gz-sim-server (for gz sim -s)
        - gz-sim-gui (for gz sim -g)
        - gz-sim-server/<world> (if world file can be extracted)
        """
        # Check if this is a gz sim command
        if 'gz sim' not in cmdline_str and 'gz-sim' not in cmdline_str:
            return ''

        # Determine if server (-s) or gui (-g)
        is_server = ' -s ' in cmdline_str or ' -s' in cmdline_str or cmdline_str.endswith(' -s')
        is_gui = ' -g ' in cmdline_str or ' -g' in cmdline_str or cmdline_str.endswith(' -g')

        base_name = 'gz-sim'
        if is_server:
            base_name = 'gz-sim-server'
            # Try to extract world name from .sdf file path
            world_match = re.search(r'/([^/]+)\.sdf', cmdline_str)
            if world_match:
                world_name = world_match.group(1)
                return f"{base_name}/{world_name}"
        elif is_gui:
            base_name = 'gz-sim-gui'

        return base_name

    def _extract_python_inline_name(self, cmdline: List[str]) -> str:
        """
        Extract a meaningful name from Python inline commands.

        Handles commands like:
        - python3 -c "from ros2cli.daemon.daemonize import main; main()"
          -> "ros2cli/daemon"
        - python3 -c "from some.module import func; func()"
          -> "some/module"
        """
        if len(cmdline) < 3:
            return ''

        # Check if this is a python -c command
        python_names = {'python', 'python3', 'python3.10', 'python3.11', 'python3.12'}
        if os.path.basename(cmdline[0]) not in python_names:
            return ''

        # Look for -c flag
        try:
            c_idx = cmdline.index('-c')
            if c_idx + 1 >= len(cmdline):
                return ''
            code = cmdline[c_idx + 1]
        except ValueError:
            return ''

        # Parse "from <module> import <name>; <name>()"
        match = re.search(r'from\s+([\w.]+)\s+import', code)
        if match:
            module_path = match.group(1)
            # Convert module.submodule to module/submodule
            # e.g., ros2cli.daemon.daemonize -> ros2cli/daemon
            parts = module_path.split('.')
            if len(parts) >= 2:
                # Use first two parts for a cleaner name
                return f"{parts[0]}/{parts[1]}"
            return parts[0]

        return ''

    def _extract_ros2_cli_name(self, cmdline: List[str]) -> str:
        """
        Extract a meaningful name from ros2 CLI tool invocations.

        Handles commands like:
        - /usr/bin/python3 /opt/ros/jazzy/bin/ros2 topic echo /joint_states
          -> "ros2 topic echo /joint_states [cli]"
        - /usr/bin/python3 /opt/ros/jazzy/bin/ros2 bag record -a
          -> "ros2 bag record [cli]"
        - /usr/bin/python3 /opt/ros/jazzy/bin/ros2 param get /node param
          -> "ros2 param get [cli]"
        """
        # Known ros2 CLI verbs (commands that are not run/launch)
        cli_verbs = {
            'topic', 'service', 'action', 'node', 'param', 'bag',
            'doctor', 'daemon', 'interface', 'component', 'lifecycle',
            'security', 'wtf', 'multicast', 'pkg', 'extension_points',
        }

        # Find 'ros2' in cmdline (could be /opt/ros/jazzy/bin/ros2 or just ros2)
        ros2_idx = None
        for i, arg in enumerate(cmdline):
            if arg.endswith('/ros2') or arg == 'ros2':
                ros2_idx = i
                break

        if ros2_idx is None:
            return ''

        # Get the verb (next argument after ros2)
        if ros2_idx + 1 >= len(cmdline):
            return ''

        verb = cmdline[ros2_idx + 1]
        if verb not in cli_verbs:
            return ''

        # Build the display name based on the verb
        parts = ['ros2', verb]

        # Get subcommand if available (e.g., 'echo' for 'topic echo')
        if ros2_idx + 2 < len(cmdline):
            subcommand = cmdline[ros2_idx + 2]
            # Skip if it starts with - (it's a flag)
            if not subcommand.startswith('-'):
                parts.append(subcommand)

                # For certain verbs, include the target (topic name, service name, etc.)
                if verb in {'topic', 'service', 'action', 'node', 'param'}:
                    if ros2_idx + 3 < len(cmdline):
                        target = cmdline[ros2_idx + 3]
                        # Skip if it's a flag
                        if not target.startswith('-'):
                            # Truncate long topic/service names
                            if len(target) > 25:
                                # Keep the last meaningful part
                                target = '...' + target[-22:]
                            parts.append(target)

        # Add [cli] suffix to indicate this is a CLI tool
        parts.append('[cli]')

        return ' '.join(parts)

    def _extract_shell_command_name(self, cmdline: List[str]) -> str:
        """
        Extract a meaningful name from shell wrapper processes.

        Handles commands like: /bin/sh -c ruby /opt/.../gz sim ...
        """
        if len(cmdline) < 3:
            return ''

        # Check if this is a shell wrapper
        shell_names = {'sh', 'bash', 'dash'}
        if os.path.basename(cmdline[0]) not in shell_names:
            return ''

        # Look for -c flag
        if '-c' not in cmdline:
            return ''

        try:
            c_index = cmdline.index('-c')
            if c_index + 1 >= len(cmdline):
                return ''

            # Get the command being executed
            shell_cmd = cmdline[c_index + 1]

            # Try to extract gz sim command from shell command
            gz_name = self._extract_gazebo_name(shell_cmd)
            if gz_name:
                return gz_name

            # For other commands, try to extract the main executable
            # Split the shell command and look for meaningful parts
            parts = shell_cmd.split()
            skip_names = {'ruby', 'python', 'python3', 'sh', 'bash'}

            for part in parts:
                basename = os.path.basename(part)
                if basename not in skip_names and not basename.startswith('-'):
                    # Check for gz command
                    if basename == 'gz' and len(parts) > parts.index(part) + 1:
                        next_part = parts[parts.index(part) + 1]
                        if next_part == 'sim':
                            return self._extract_gazebo_name(shell_cmd)
                    return basename

            return ''
        except (ValueError, IndexError):
            return ''

    def _extract_namespace(self, proc: psutil.Process) -> str:
        """Try to extract ROS namespace from process info."""
        try:
            cmdline = ' '.join(proc.cmdline())

            # Look for __ns:=<namespace>
            match = re.search(r'__ns:=(\S+)', cmdline)
            if match:
                return match.group(1)

            # Try environment variable
            try:
                environ = proc.environ()
                if 'ROS_NAMESPACE' in environ:
                    return environ['ROS_NAMESPACE']
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

            return ''

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return ''

    def _get_container_name(self, pid: int) -> str:
        """
        Get Docker container name for a PID.

        Returns empty string if not in a container.
        """
        try:
            # Read cgroup to detect if in container
            with open(f'/proc/{pid}/cgroup', 'r') as f:
                cgroup = f.read()

            # Look for docker pattern: /docker/<container_id>
            match = re.search(r'/docker/([a-f0-9]{12,64})', cgroup)
            if not match:
                return ""

            container_id = match.group(1)[:12]

            # Try to get container name via docker API (optional)
            try:
                import docker
                client = docker.from_env()
                container = client.containers.get(container_id)
                return container.name
            except Exception:
                # Return short ID if we can't get the name
                return container_id

        except (FileNotFoundError, PermissionError, IOError):
            return ""
