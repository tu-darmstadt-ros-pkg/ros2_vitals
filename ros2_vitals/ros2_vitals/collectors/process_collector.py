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
        # Cache Process objects to enable proper cpu_percent() tracking
        # cpu_percent() needs to be called on the same Process object over time
        self._process_cache: Dict[int, psutil.Process] = {}
        # Cache last CPU values for processes (needed for proper CPU measurement)
        self._cpu_cache: Dict[int, float] = {}
        # Track which PIDs we've seen to clean up stale cache entries
        self._last_seen_pids: Set[int] = set()
        # Cache container names (rarely changes)
        self._container_cache: Dict[int, str] = {}
        # Cache GPU process memory (updated once per cycle, not per process)
        self._gpu_process_cache: Dict[int, Dict[str, Any]] = {}

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

        # Build GPU process cache once per cycle (expensive NVML call)
        self._refresh_gpu_process_cache()

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

                # Skip shell wrapper processes (e.g., /bin/sh -c ...)
                # These are intermediate processes, we'll capture their children instead
                if self._is_shell_wrapper(cmdline):
                    continue

                # Check if this is a ROS process
                if not self._is_ros_process(cmdline_str):
                    continue

                # Use cached Process object if available for proper CPU tracking
                if pid in self._process_cache:
                    cached_proc = self._process_cache[pid]
                    # Verify it's still the same process
                    try:
                        if cached_proc.create_time() == proc.create_time():
                            proc = cached_proc
                        else:
                            # PID was reused, update cache
                            self._process_cache[pid] = proc
                            self._cpu_cache.pop(pid, None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        self._process_cache[pid] = proc
                        self._cpu_cache.pop(pid, None)
                else:
                    self._process_cache[pid] = proc

                current_pids.add(pid)

                # Collect process stats
                proc_info = self._collect_process_stats(proc, include_children)
                if proc_info:
                    ros_processes.append(proc_info)

                    # Mark children as seen and track them
                    if include_children:
                        for child_pid in proc_info.get('child_pids', []):
                            seen_pids.add(child_pid)
                            current_pids.add(child_pid)

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # Clean up stale cache entries (processes that no longer exist)
        stale_pids = self._last_seen_pids - current_pids
        for pid in stale_pids:
            self._process_cache.pop(pid, None)
            self._cpu_cache.pop(pid, None)
            self._rate_calc.remove_key(f"proc.{pid}.read")
            self._rate_calc.remove_key(f"proc.{pid}.write")

        self._last_seen_pids = current_pids

        # Clean up stale container cache entries
        for pid in stale_pids:
            self._container_cache.pop(pid, None)

        return ros_processes

    def _refresh_gpu_process_cache(self):
        """Refresh GPU process memory cache (called once per collection cycle)."""
        self._gpu_process_cache.clear()
        if not self._gpu_collector or not self._gpu_collector.available:
            return

        # Import here to avoid issues if pynvml not available
        try:
            from pynvml import nvmlDeviceGetHandleByIndex, nvmlDeviceGetComputeRunningProcesses
        except ImportError:
            return

        for i in range(self._gpu_collector._device_count):
            try:
                handle = nvmlDeviceGetHandleByIndex(i)
                processes = nvmlDeviceGetComputeRunningProcesses(handle)
                for proc in processes:
                    self._gpu_process_cache[proc.pid] = {
                        'gpu_index': i,
                        'memory_bytes': proc.usedGpuMemory or 0,
                    }
            except Exception:
                continue

    def _get_process_gpu_memory(self, pid: int) -> Optional[Dict[str, Any]]:
        """Get GPU memory from cache (O(1) lookup instead of O(n*m))."""
        return self._gpu_process_cache.get(pid)

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
        self, proc: psutil.Process, include_children: bool
    ) -> Optional[Dict[str, Any]]:
        """Collect statistics for a single process."""
        try:
            pid = proc.pid
            cmdline = ' '.join(proc.cmdline())

            # Check if this is a launch process
            is_launch = self._is_launch_process(cmdline)
            launch_name = self._get_launch_name(cmdline) if is_launch else ""

            # Basic info - use cached container name
            if pid not in self._container_cache:
                self._container_cache[pid] = self._get_container_name(pid)

            info = {
                'pid': pid,
                'cmdline': cmdline[:500],  # Limit length
                'node_name': self._extract_node_name(proc),
                'node_namespace': self._extract_namespace(proc),
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
                            # Extract node name from already-fetched cmdline
                            node_name = self._extract_node_name_from_cmdline(child_cmdline_list, child_cmdline)
                            # Only add if we can extract a meaningful node name
                            if node_name:
                                child_node_info = {
                                    'pid': child_pid,
                                    'cmdline': child_cmdline[:500],
                                    'node_name': node_name,
                                    'node_namespace': self._extract_namespace_from_cmdline(child_cmdline),
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

    def _extract_node_name_from_cmdline(self, cmdline: List[str], cmdline_str: str) -> str:
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

        # Final fallback: use executable name
        if cmdline:
            basename = os.path.basename(cmdline[0])
            if basename not in {'python3', 'python', 'bash', 'sh'}:
                return basename

        return ''

    def _extract_namespace_from_cmdline(self, cmdline_str: str) -> str:
        """Extract ROS namespace from pre-fetched cmdline string."""
        # Look for __ns:=<namespace>
        match = re.search(r'__ns:=(\S+)', cmdline_str)
        if match:
            return match.group(1)

        # Look for --namespace <ns> in launch command
        match = re.search(r'--namespace\s+(\S+)', cmdline_str)
        if match:
            return match.group(1)

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
