"""
Terminal UI for ROS 2 Vitals system monitor.

A curses-based interface for viewing system status across multiple hosts.
"""

import curses
import threading
import time
from typing import Dict, Optional
from dataclasses import dataclass, field

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy

from ros2_vitals_msgs.msg import SystemStatus
from ros2_vitals_msgs.srv import KillProcess


@dataclass
class HostData:
    """Cached data for a host."""
    status: Optional[SystemStatus] = None
    last_update: float = 0.0


class VitalsMonitorNode(Node):
    """ROS node that subscribes to vitals status messages."""

    def __init__(self):
        super().__init__('vitals_monitor')

        self.declare_parameter('topic', '/vitals/status')
        topic = self.get_parameter('topic').value

        # Use wildcard subscription to get all vitals topics
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            depth=10,
        )

        self._hosts: Dict[str, HostData] = {}
        self._lock = threading.Lock()

        self._subscription = self.create_subscription(
            SystemStatus, topic, self._status_callback, qos
        )

    def _status_callback(self, msg: SystemStatus):
        """Handle incoming status message."""
        with self._lock:
            hostname = msg.hostname
            self._hosts[hostname] = HostData(status=msg, last_update=time.time())

    def get_hosts(self) -> Dict[str, HostData]:
        """Get current host data."""
        with self._lock:
            return dict(self._hosts)


def format_bytes(bytes_val: int) -> str:
    """Format bytes as human-readable string."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.1f} GB"


def format_bytes_rate(bytes_per_sec: float) -> str:
    """Format bytes/sec as human-readable string."""
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    elif bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    else:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"


def draw_progress_bar(width: int, percent: float) -> str:
    """Draw a text-based progress bar."""
    filled = int(width * percent / 100)
    empty = width - filled
    return '█' * filled + '░' * empty


class TerminalUI:
    """Curses-based terminal UI for vitals monitor."""

    def __init__(self, node: VitalsMonitorNode):
        self._node = node
        self._running = True
        self._selected_host = 0
        self._selected_process = 0
        self._paused = False

    def run(self, stdscr):
        """Main UI loop."""
        curses.curs_set(0)  # Hide cursor
        stdscr.nodelay(True)  # Non-blocking input
        stdscr.timeout(100)  # Refresh every 100ms

        # Initialize colors
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)   # Good
        curses.init_pair(2, curses.COLOR_YELLOW, -1)  # Warning
        curses.init_pair(3, curses.COLOR_RED, -1)     # Error
        curses.init_pair(4, curses.COLOR_CYAN, -1)    # Info
        curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Header

        while self._running:
            # Handle input
            try:
                key = stdscr.getch()
                self._handle_input(key)
            except curses.error:
                pass

            # Draw UI
            if not self._paused:
                self._draw(stdscr)

        return 0

    def _handle_input(self, key: int):
        """Handle keyboard input."""
        if key == ord('q') or key == ord('Q'):
            self._running = False
        elif key == ord('p') or key == ord('P'):
            self._paused = not self._paused
        elif key == curses.KEY_UP:
            self._selected_process = max(0, self._selected_process - 1)
        elif key == curses.KEY_DOWN:
            self._selected_process += 1
        elif key == curses.KEY_LEFT:
            self._selected_host = max(0, self._selected_host - 1)
        elif key == curses.KEY_RIGHT:
            hosts = self._node.get_hosts()
            self._selected_host = min(len(hosts) - 1, self._selected_host + 1)

    def _draw(self, stdscr):
        """Draw the UI."""
        # Use erase() instead of clear() to avoid flicker
        # erase() doesn't cause a full terminal refresh like clear() does
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        hosts = self._node.get_hosts()
        host_names = sorted(hosts.keys())

        # Title bar
        title = " ROS 2 Vitals Monitor "
        controls = " [Q]uit [P]ause [←→] Host [↑↓] Process "
        stdscr.attron(curses.color_pair(5))
        stdscr.addstr(0, 0, ' ' * width)
        stdscr.addstr(0, 2, title, curses.A_BOLD)
        stdscr.addstr(0, width - len(controls) - 2, controls)
        stdscr.attroff(curses.color_pair(5))

        if not hosts:
            stdscr.addstr(height // 2, width // 2 - 15, "Waiting for vitals data...",
                          curses.color_pair(4))
            stdscr.refresh()
            return

        # Host selector
        row = 2
        stdscr.addstr(row, 2, "HOSTS:", curses.A_BOLD)
        row += 1

        for i, hostname in enumerate(host_names):
            host_data = hosts[hostname]
            age = time.time() - host_data.last_update

            prefix = "► " if i == self._selected_host else "  "
            status_color = curses.color_pair(1) if age < 5 else curses.color_pair(3)

            age_str = f"({age:.1f}s ago)" if age < 60 else f"({age / 60:.0f}m ago)"
            stdscr.addstr(row, 2, prefix + hostname, curses.A_BOLD if i == self._selected_host else 0)
            stdscr.addstr(row, 2 + len(prefix) + len(hostname) + 1, age_str, status_color)
            row += 1

        row += 1

        # Selected host details
        if 0 <= self._selected_host < len(host_names):
            hostname = host_names[self._selected_host]
            host_data = hosts[hostname]

            if host_data.status:
                self._draw_host_details(stdscr, row, width, host_data.status)

        stdscr.refresh()

    def _draw_host_details(self, stdscr, start_row: int, width: int, status: SystemStatus):
        """Draw details for a single host."""
        row = start_row

        # System overview
        stdscr.addstr(row, 2, f"═══ {status.hostname} ═══", curses.A_BOLD)
        row += 2

        # CPU
        cpu_bar = draw_progress_bar(20, status.cpu_percent)
        cpu_color = (curses.color_pair(1) if status.cpu_percent < 70
                     else curses.color_pair(2) if status.cpu_percent < 90
                     else curses.color_pair(3))
        stdscr.addstr(row, 2, f"CPU:  [{cpu_bar}] {status.cpu_percent:5.1f}%", cpu_color)
        stdscr.addstr(row, 45, f"({status.cpu_count} cores)")
        if status.cpu_temperature_celsius > 0:
            temp_color = (curses.color_pair(1) if status.cpu_temperature_celsius < 70
                          else curses.color_pair(2) if status.cpu_temperature_celsius < 85
                          else curses.color_pair(3))
            stdscr.addstr(row, 60, f"{status.cpu_temperature_celsius:.0f}°C", temp_color)
        row += 1

        # RAM
        ram_percent = (status.ram_used_bytes / status.ram_total_bytes * 100
                       if status.ram_total_bytes > 0 else 0)
        ram_bar = draw_progress_bar(20, ram_percent)
        ram_color = (curses.color_pair(1) if ram_percent < 75
                     else curses.color_pair(2) if ram_percent < 90
                     else curses.color_pair(3))
        ram_used = format_bytes(status.ram_used_bytes)
        ram_total = format_bytes(status.ram_total_bytes)
        stdscr.addstr(row, 2, f"RAM:  [{ram_bar}] {ram_percent:5.1f}%", ram_color)
        stdscr.addstr(row, 45, f"({ram_used} / {ram_total})")
        row += 1

        # GPU (if available)
        if status.gpus:
            for gpu in status.gpus:
                gpu_mem_percent = (gpu.memory_used_bytes / gpu.memory_total_bytes * 100
                                   if gpu.memory_total_bytes > 0 else 0)
                gpu_bar = draw_progress_bar(20, gpu.utilization_percent)
                mem_bar = draw_progress_bar(10, gpu_mem_percent)

                stdscr.addstr(row, 2, f"GPU{gpu.index}: [{gpu_bar}] {gpu.utilization_percent:5.1f}%")
                stdscr.addstr(row, 45, f"VRAM: [{mem_bar}] {format_bytes(gpu.memory_used_bytes)}")
                if gpu.temperature_celsius > 0:
                    temp_color = (curses.color_pair(1) if gpu.temperature_celsius < 70
                                  else curses.color_pair(2) if gpu.temperature_celsius < 85
                                  else curses.color_pair(3))
                    stdscr.addstr(row, 75, f"{gpu.temperature_celsius:.0f}°C", temp_color)
                row += 1

        # Load average
        stdscr.addstr(row, 2, f"Load: {status.load_avg_1min:.2f} {status.load_avg_5min:.2f} {status.load_avg_15min:.2f}")

        # Uptime
        uptime_hours = status.uptime_seconds / 3600
        if uptime_hours < 24:
            uptime_str = f"{uptime_hours:.1f}h"
        else:
            uptime_str = f"{uptime_hours / 24:.1f}d"
        stdscr.addstr(row, 45, f"Uptime: {uptime_str}")
        row += 2

        # Network
        if status.network_interfaces:
            stdscr.addstr(row, 2, "Network:", curses.A_BOLD)
            row += 1
            for iface in status.network_interfaces:
                if iface.is_up:
                    up = format_bytes_rate(iface.bytes_sent_per_sec)
                    down = format_bytes_rate(iface.bytes_recv_per_sec)
                    stdscr.addstr(row, 4, f"{iface.name}: ↑{up} ↓{down}")
                    row += 1
            row += 1

        # Processes - flatten launch groups but show child nodes
        if status.processes:
            # Build flat list of displayable nodes
            display_procs = []
            for proc in status.processes:
                if proc.is_launch_process:
                    # Add launch group header
                    display_procs.append({
                        'type': 'launch',
                        'name': proc.launch_name or proc.node_name,
                        'cpu': proc.cpu_percent,
                        'ram': proc.ram_bytes,
                        'gpu_index': proc.gpu_index,
                        'gpu_mem': proc.gpu_memory_bytes,
                        'disk_rate': proc.disk_read_bytes_per_sec + proc.disk_write_bytes_per_sec,
                    })
                    # Add child nodes
                    for child in proc.child_nodes:
                        display_procs.append({
                            'type': 'node',
                            'name': child.node_name,
                            'cpu': child.cpu_percent,
                            'ram': child.ram_bytes,
                            'gpu_index': child.gpu_index,
                            'gpu_mem': child.gpu_memory_bytes,
                            'disk_rate': child.disk_read_bytes_per_sec + child.disk_write_bytes_per_sec,
                            'parent': proc.launch_name or proc.node_name,
                        })
                else:
                    # Standalone process
                    display_procs.append({
                        'type': 'node',
                        'name': proc.node_name or proc.cmdline[:40],
                        'cpu': proc.cpu_percent,
                        'ram': proc.ram_bytes,
                        'gpu_index': proc.gpu_index,
                        'gpu_mem': proc.gpu_memory_bytes,
                        'disk_rate': proc.disk_read_bytes_per_sec + proc.disk_write_bytes_per_sec,
                    })

            stdscr.addstr(row, 2, f"ROS Processes ({len(display_procs)}):", curses.A_BOLD)
            row += 1

            # Header
            stdscr.addstr(row, 4, f"{'Node':<50} {'CPU':>7} {'RAM':>10} {'GPU':>10} {'Disk':>12}",
                          curses.A_DIM)
            row += 1

            # Process list
            max_procs = min(len(display_procs), 15)
            for i, proc in enumerate(display_procs[:max_procs]):
                if proc['type'] == 'launch':
                    # Launch group header with rocket emoji
                    node_name = f"\U0001F680 {proc['name']}"
                    attr_extra = curses.A_BOLD
                else:
                    # Node - indent if it has a parent launch
                    if 'parent' in proc:
                        node_name = f"  \u251C\u2500 {proc['name']}"  # ├─
                    else:
                        node_name = proc['name']
                    attr_extra = 0

                if len(node_name) > 48:
                    node_name = node_name[:45] + "..."

                gpu_str = format_bytes(proc['gpu_mem']) if proc['gpu_index'] >= 0 else "-"

                line = (f"{node_name:<50} {proc['cpu']:6.1f}% "
                        f"{format_bytes(proc['ram']):>10} {gpu_str:>10} "
                        f"{format_bytes_rate(proc['disk_rate']):>12}")

                attrs = curses.A_REVERSE if i == self._selected_process else attr_extra
                stdscr.addstr(row, 4, line, attrs)
                row += 1


def main(args=None):
    """Main entry point."""
    rclpy.init(args=args)
    node = VitalsMonitorNode()

    # Run ROS spinning in background thread
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    # Run terminal UI
    ui = TerminalUI(node)
    try:
        curses.wrapper(ui.run)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
