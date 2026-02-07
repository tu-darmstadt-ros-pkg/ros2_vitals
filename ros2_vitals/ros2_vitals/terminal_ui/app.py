"""
Terminal UI for ROS 2 Vitals system monitor.

A curses-based interface for viewing system status across multiple hosts.
"""

import curses
import threading
import time
from typing import Dict, Optional, List
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
        self._scroll_offset = 0
        self._paused = False
        self._display_procs: List[dict] = []

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
                self._handle_input(key, stdscr)
            except curses.error:
                pass

            # Draw UI
            if not self._paused:
                self._draw(stdscr)

        return 0

    def _handle_input(self, key: int, stdscr):
        """Handle keyboard input."""
        height, width = stdscr.getmaxyx()

        if key == ord('q') or key == ord('Q'):
            self._running = False
        elif key == ord('p') or key == ord('P'):
            self._paused = not self._paused
        elif key == curses.KEY_UP:
            if self._selected_process > 0:
                self._selected_process -= 1
                # Adjust scroll if selection goes above visible area
                if self._selected_process < self._scroll_offset:
                    self._scroll_offset = self._selected_process
        elif key == curses.KEY_DOWN:
            if self._selected_process < len(self._display_procs) - 1:
                self._selected_process += 1
                # Adjust scroll if selection goes below visible area
                visible_procs = self._get_visible_process_count(height)
                if self._selected_process >= self._scroll_offset + visible_procs:
                    self._scroll_offset = self._selected_process - visible_procs + 1
        elif key == curses.KEY_LEFT:
            self._selected_host = max(0, self._selected_host - 1)
            self._selected_process = 0
            self._scroll_offset = 0
        elif key == curses.KEY_RIGHT:
            hosts = self._node.get_hosts()
            self._selected_host = min(len(hosts) - 1, self._selected_host + 1)
            self._selected_process = 0
            self._scroll_offset = 0
        elif key == curses.KEY_PPAGE:  # Page Up
            visible_procs = self._get_visible_process_count(height)
            self._scroll_offset = max(0, self._scroll_offset - visible_procs)
            self._selected_process = max(0, self._selected_process - visible_procs)
        elif key == curses.KEY_NPAGE:  # Page Down
            visible_procs = self._get_visible_process_count(height)
            max_scroll = max(0, len(self._display_procs) - visible_procs)
            self._scroll_offset = min(max_scroll, self._scroll_offset + visible_procs)
            self._selected_process = min(len(self._display_procs) - 1, self._selected_process + visible_procs)
        elif key == curses.KEY_HOME:
            self._scroll_offset = 0
            self._selected_process = 0
        elif key == curses.KEY_END:
            visible_procs = self._get_visible_process_count(height)
            self._scroll_offset = max(0, len(self._display_procs) - visible_procs)
            self._selected_process = len(self._display_procs) - 1 if self._display_procs else 0
        elif key == curses.KEY_RESIZE:
            # Handle terminal resize
            stdscr.clear()

    def _get_visible_process_count(self, height: int) -> int:
        """Calculate how many processes can be displayed given terminal height."""
        # Reserve space for: title(1) + hosts(~5) + system stats(~12) + header(2) + status(1)
        reserved = 21
        return max(1, height - reserved)

    def _draw(self, stdscr):
        """Draw the UI."""
        # Use erase() instead of clear() to avoid flicker
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        hosts = self._node.get_hosts()
        host_names = sorted(hosts.keys())

        # Title bar
        title = " ROS 2 Vitals Monitor "
        controls = " [Q]uit [P]ause [<>] Host [^v] Process [PgUp/Dn] Scroll "
        try:
            stdscr.attron(curses.color_pair(5))
            stdscr.addstr(0, 0, ' ' * (width - 1))
            stdscr.addstr(0, 2, title, curses.A_BOLD)
            if width > len(controls) + len(title) + 4:
                stdscr.addstr(0, width - len(controls) - 2, controls)
            stdscr.attroff(curses.color_pair(5))
        except curses.error:
            pass

        if not hosts:
            try:
                stdscr.addstr(height // 2, max(0, width // 2 - 15), "Waiting for vitals data...",
                              curses.color_pair(4))
            except curses.error:
                pass
            stdscr.refresh()
            return

        # Host selector
        row = 2
        try:
            stdscr.addstr(row, 2, "HOSTS:", curses.A_BOLD)
        except curses.error:
            pass
        row += 1

        for i, hostname in enumerate(host_names):
            if row >= height - 1:
                break
            host_data = hosts[hostname]
            age = time.time() - host_data.last_update

            prefix = "> " if i == self._selected_host else "  "
            status_color = curses.color_pair(1) if age < 5 else curses.color_pair(3)

            age_str = f"({age:.1f}s ago)" if age < 60 else f"({age / 60:.0f}m ago)"
            try:
                stdscr.addstr(row, 2, prefix + hostname, curses.A_BOLD if i == self._selected_host else 0)
                stdscr.addstr(row, 2 + len(prefix) + len(hostname) + 1, age_str, status_color)
            except curses.error:
                pass
            row += 1

        row += 1

        # Selected host details
        if 0 <= self._selected_host < len(host_names):
            hostname = host_names[self._selected_host]
            host_data = hosts[hostname]

            if host_data.status:
                self._draw_host_details(stdscr, row, width, height, host_data.status)

        stdscr.refresh()

    def _draw_host_details(self, stdscr, start_row: int, width: int, height: int, status: SystemStatus):
        """Draw details for a single host."""
        row = start_row

        # System overview
        try:
            header = f"=== {status.hostname} ==="
            stdscr.addstr(row, 2, header[:width-4], curses.A_BOLD)
        except curses.error:
            pass
        row += 2

        if row >= height - 1:
            return

        # CPU
        try:
            cpu_bar = draw_progress_bar(20, status.cpu_percent)
            cpu_color = (curses.color_pair(1) if status.cpu_percent < 70
                         else curses.color_pair(2) if status.cpu_percent < 90
                         else curses.color_pair(3))
            stdscr.addstr(row, 2, f"CPU:  [{cpu_bar}] {status.cpu_percent:5.1f}%", cpu_color)
            if width > 55:
                stdscr.addstr(row, 45, f"({status.cpu_count} cores)")
            if width > 70 and status.cpu_temperature_celsius > 0:
                temp_color = (curses.color_pair(1) if status.cpu_temperature_celsius < 70
                              else curses.color_pair(2) if status.cpu_temperature_celsius < 85
                              else curses.color_pair(3))
                stdscr.addstr(row, 60, f"{status.cpu_temperature_celsius:.0f}C", temp_color)
        except curses.error:
            pass
        row += 1

        if row >= height - 1:
            return

        # RAM
        try:
            ram_percent = (status.ram_used_bytes / status.ram_total_bytes * 100
                           if status.ram_total_bytes > 0 else 0)
            ram_bar = draw_progress_bar(20, ram_percent)
            ram_color = (curses.color_pair(1) if ram_percent < 75
                         else curses.color_pair(2) if ram_percent < 90
                         else curses.color_pair(3))
            ram_used = format_bytes(status.ram_used_bytes)
            ram_total = format_bytes(status.ram_total_bytes)
            stdscr.addstr(row, 2, f"RAM:  [{ram_bar}] {ram_percent:5.1f}%", ram_color)
            if width > 70:
                stdscr.addstr(row, 45, f"({ram_used} / {ram_total})")
        except curses.error:
            pass
        row += 1

        if row >= height - 1:
            return

        # GPU (if available)
        if status.gpus:
            for gpu in status.gpus:
                if row >= height - 1:
                    break
                try:
                    gpu_mem_percent = (gpu.memory_used_bytes / gpu.memory_total_bytes * 100
                                       if gpu.memory_total_bytes > 0 else 0)
                    gpu_bar = draw_progress_bar(20, gpu.utilization_percent)
                    mem_bar = draw_progress_bar(10, gpu_mem_percent)

                    stdscr.addstr(row, 2, f"GPU{gpu.index}: [{gpu_bar}] {gpu.utilization_percent:5.1f}%")
                    if width > 75:
                        stdscr.addstr(row, 45, f"VRAM: [{mem_bar}] {format_bytes(gpu.memory_used_bytes)}")
                    if width > 85 and gpu.temperature_celsius > 0:
                        temp_color = (curses.color_pair(1) if gpu.temperature_celsius < 70
                                      else curses.color_pair(2) if gpu.temperature_celsius < 85
                                      else curses.color_pair(3))
                        stdscr.addstr(row, 75, f"{gpu.temperature_celsius:.0f}C", temp_color)
                except curses.error:
                    pass
                row += 1

        if row >= height - 1:
            return

        # Load average
        try:
            stdscr.addstr(row, 2, f"Load: {status.load_avg_1min:.2f} {status.load_avg_5min:.2f} {status.load_avg_15min:.2f}")

            # Uptime
            uptime_hours = status.uptime_seconds / 3600
            if uptime_hours < 24:
                uptime_str = f"{uptime_hours:.1f}h"
            else:
                uptime_str = f"{uptime_hours / 24:.1f}d"
            if width > 60:
                stdscr.addstr(row, 45, f"Uptime: {uptime_str}")
        except curses.error:
            pass
        row += 2

        if row >= height - 1:
            return

        # Build flat list of displayable nodes
        self._display_procs = []
        if status.processes:
            for proc in status.processes:
                if proc.is_launch_process:
                    # Add launch group header
                    self._display_procs.append({
                        'type': 'launch',
                        'name': proc.launch_name or proc.node_name,
                        'cpu': proc.cpu_percent,
                        'ram': proc.ram_bytes,
                        'gpu_index': proc.gpu_index,
                        'gpu_mem': proc.gpu_memory_bytes,
                        'disk_rate': proc.disk_read_bytes_per_sec + proc.disk_write_bytes_per_sec,
                        'child_count': len(proc.child_nodes),
                    })
                    # Add child nodes
                    for child in proc.child_nodes:
                        self._display_procs.append({
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
                    self._display_procs.append({
                        'type': 'node',
                        'name': proc.node_name or proc.cmdline[:40],
                        'cpu': proc.cpu_percent,
                        'ram': proc.ram_bytes,
                        'gpu_index': proc.gpu_index,
                        'gpu_mem': proc.gpu_memory_bytes,
                        'disk_rate': proc.disk_read_bytes_per_sec + proc.disk_write_bytes_per_sec,
                    })

        # Clamp selection to valid range
        if self._display_procs:
            self._selected_process = min(self._selected_process, len(self._display_procs) - 1)
        else:
            self._selected_process = 0

        # Process header
        proc_count = len(self._display_procs)
        try:
            stdscr.addstr(row, 2, f"ROS Processes ({proc_count}):", curses.A_BOLD)
        except curses.error:
            pass
        row += 1

        if row >= height - 1:
            return

        # Column header
        try:
            header = f"{'Node':<40} {'CPU':>7} {'RAM':>10} {'GPU':>10} {'Disk':>12}"
            stdscr.addstr(row, 4, header[:width-6], curses.A_DIM)
        except curses.error:
            pass
        row += 1

        # Calculate visible process count
        visible_procs = height - row - 1  # Leave 1 row for status
        if visible_procs < 1:
            return

        # Adjust scroll offset if needed
        max_scroll = max(0, len(self._display_procs) - visible_procs)
        self._scroll_offset = min(self._scroll_offset, max_scroll)

        # Process list with scrolling
        for i, proc in enumerate(self._display_procs[self._scroll_offset:self._scroll_offset + visible_procs]):
            if row >= height - 1:
                break

            actual_idx = i + self._scroll_offset

            if proc['type'] == 'launch':
                # Launch group header
                child_count = proc.get('child_count', 0)
                node_name = f"[L] {proc['name']} ({child_count})"
                attr_extra = curses.A_BOLD
            else:
                # Node - indent if it has a parent launch
                if 'parent' in proc:
                    node_name = f"  +- {proc['name']}"
                else:
                    node_name = proc['name']
                attr_extra = 0

            # Truncate name based on available width
            max_name_len = min(38, width - 50)
            if len(node_name) > max_name_len:
                node_name = node_name[:max_name_len-3] + "..."

            gpu_str = format_bytes(proc['gpu_mem']) if proc['gpu_index'] >= 0 else "-"

            try:
                line = (f"{node_name:<40} {proc['cpu']:6.1f}% "
                        f"{format_bytes(proc['ram']):>10} {gpu_str:>10} "
                        f"{format_bytes_rate(proc['disk_rate']):>12}")

                # Truncate line to fit width
                line = line[:width-6]

                attrs = curses.A_REVERSE if actual_idx == self._selected_process else attr_extra
                stdscr.addstr(row, 4, line, attrs)
            except curses.error:
                pass
            row += 1

        # Scroll indicator
        if len(self._display_procs) > visible_procs:
            try:
                scroll_info = f"[{self._scroll_offset + 1}-{min(self._scroll_offset + visible_procs, len(self._display_procs))}/{len(self._display_procs)}]"
                stdscr.addstr(height - 1, width - len(scroll_info) - 2, scroll_info, curses.A_DIM)
            except curses.error:
                pass


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
