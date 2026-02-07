"""Collector for ROS process discovery and statistics."""

import os
import re
import time
from typing import List, Dict, Any, Optional, Set
import logging

import psutil

from ..utils.rate_calculator import RateCalculator
from .gpu_collector import GpuCollector

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

    def __init__(self, gpu_collector: Optional[GpuCollector] = None):
        self._rate_calc = RateCalculator()
        self._gpu_collector = gpu_collector
        self._process_cache: Dict[int, psutil.Process] = {}

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

        # Find all ROS-related processes
        for proc in psutil.process_iter(['pid', 'ppid', 'name', 'cmdline']):
            try:
                pid = proc.info['pid']

                # Skip if already processed as a child
                if pid in seen_pids:
                    continue

                cmdline = proc.info['cmdline']
                if not cmdline:
                    continue

                cmdline_str = ' '.join(cmdline)

                # Check if this is a ROS process
                if not self._is_ros_process(cmdline_str):
                    continue

                # Collect process stats
                proc_info = self._collect_process_stats(proc, include_children)
                if proc_info:
                    ros_processes.append(proc_info)

                    # Mark children as seen to avoid double-counting
                    if include_children:
                        for child_pid in proc_info.get('child_pids', []):
                            seen_pids.add(child_pid)

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        return ros_processes

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

    def _collect_process_stats(
        self, proc: psutil.Process, include_children: bool
    ) -> Optional[Dict[str, Any]]:
        """Collect statistics for a single process."""
        try:
            pid = proc.pid
            cmdline = ' '.join(proc.cmdline())

            # Check if this is a launch process
            is_launch = self._is_launch_process(cmdline)
            launch_name = self._get_launch_name(cmdline) if is_launch else ""

            # Basic info
            info = {
                'pid': pid,
                'cmdline': cmdline[:500],  # Limit length
                'node_name': self._extract_node_name(proc),
                'node_namespace': self._extract_namespace(proc),
                'container_name': self._get_container_name(pid),
                'child_pids': [],
                'is_launch_process': is_launch,
                'launch_name': launch_name,
                'child_nodes': [],  # Will be populated for launch processes
            }

            # Status - use more accurate detection
            status = proc.status()
            # If process has any CPU usage, consider it running even if kernel says sleeping
            # Most ROS nodes are event-driven and appear as "sleeping" when waiting for messages
            info['status'] = status
            info['num_threads'] = proc.num_threads()
            info['create_time'] = proc.create_time()

            # CPU (needs to be called twice with interval for accurate reading)
            # We rely on the node calling this periodically
            try:
                info['cpu_percent'] = proc.cpu_percent()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                info['cpu_percent'] = 0.0

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

            # Open files and connections
            try:
                info['open_files_count'] = len(proc.open_files())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                info['open_files_count'] = 0

            try:
                info['network_connections_count'] = len(proc.connections())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                info['network_connections_count'] = 0

            # GPU memory
            info['gpu_index'] = -1
            info['gpu_memory_bytes'] = 0
            if self._gpu_collector and self._gpu_collector.available:
                gpu_info = self._gpu_collector.get_process_gpu_memory(pid)
                if gpu_info:
                    info['gpu_index'] = gpu_info['gpu_index']
                    info['gpu_memory_bytes'] = gpu_info['memory_bytes']

            # Aggregate children stats
            if include_children:
                self._aggregate_children(proc, info)

            return info

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
            logger.debug(f"Failed to collect stats for process: {e}")
            return None

    def _aggregate_children(self, proc: psutil.Process, info: Dict[str, Any]):
        """Aggregate statistics from child processes."""
        try:
            children = proc.children(recursive=True)
            child_pids = []
            child_nodes = []

            for child in children:
                try:
                    child_pids.append(child.pid)

                    # Collect individual stats for this child
                    child_cpu = child.cpu_percent()
                    child_mem = child.memory_info().rss

                    # CPU
                    info['cpu_percent'] += child_cpu

                    # RAM
                    info['ram_bytes'] += child_mem

                    # Disk I/O
                    child_disk_read = 0
                    child_disk_write = 0
                    child_disk_read_rate = 0.0
                    child_disk_write_rate = 0.0
                    try:
                        io = child.io_counters()
                        info['disk_read_bytes_total'] += io.read_bytes
                        info['disk_write_bytes_total'] += io.write_bytes
                        child_disk_read = io.read_bytes
                        child_disk_write = io.write_bytes
                        child_disk_read_rate = self._rate_calc.calculate_rate(
                            f"proc.{child.pid}.read", io.read_bytes
                        )
                        child_disk_write_rate = self._rate_calc.calculate_rate(
                            f"proc.{child.pid}.write", io.write_bytes
                        )
                    except (psutil.AccessDenied, AttributeError):
                        pass

                    # GPU memory
                    child_gpu_index = -1
                    child_gpu_mem = 0
                    if self._gpu_collector and self._gpu_collector.available:
                        gpu_info = self._gpu_collector.get_process_gpu_memory(child.pid)
                        if gpu_info:
                            info['gpu_memory_bytes'] += gpu_info['memory_bytes']
                            child_gpu_index = gpu_info['gpu_index']
                            child_gpu_mem = gpu_info['memory_bytes']
                            # Use first GPU found if parent doesn't have one
                            if info['gpu_index'] == -1:
                                info['gpu_index'] = gpu_info['gpu_index']

                    # For launch processes, collect individual child node info
                    if info.get('is_launch_process', False):
                        child_cmdline = ' '.join(child.cmdline())
                        # Skip if this child is itself a launch process
                        if not self._is_launch_process(child_cmdline):
                            child_node_info = {
                                'pid': child.pid,
                                'cmdline': child_cmdline[:500],
                                'node_name': self._extract_node_name(child),
                                'node_namespace': self._extract_namespace(child),
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
                                'disk_read_bytes_total': child_disk_read,
                                'disk_write_bytes_total': child_disk_write,
                                'disk_read_bytes_per_sec': child_disk_read_rate,
                                'disk_write_bytes_per_sec': child_disk_write_rate,
                                'open_files_count': 0,
                                'network_connections_count': 0,
                                'gpu_index': child_gpu_index,
                                'gpu_memory_bytes': child_gpu_mem,
                            }
                            # Only add if we can extract a meaningful node name
                            if child_node_info['node_name']:
                                child_nodes.append(child_node_info)

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

            info['child_pids'] = child_pids
            info['child_nodes'] = child_nodes

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

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
                # Use launch emoji to distinguish launch processes
                return f"\U0001F680 {package}/{launch_name}"  # 🚀

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
