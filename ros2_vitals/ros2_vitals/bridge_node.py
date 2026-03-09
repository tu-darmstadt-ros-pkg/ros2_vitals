"""
ROS 2 Vitals Bridge Node.

Connects to the vitals-daemon via Unix socket and publishes system metrics
as ROS 2 messages. The daemon runs as root and handles eBPF and /proc
scanning; this bridge is a normal unprivileged ROS node.
"""

import json
import socket
import struct
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy

from ros2_vitals_msgs.msg import (
    SystemStatus,
    GpuStatus,
    NetworkInterface,
    DiskStatus,
    ProcessStatus,
    ChildProcessStatus,
)
from ros2_vitals_msgs.srv import KillProcess

HEADER_FMT = '!I'
HEADER_SIZE = struct.calcsize(HEADER_FMT)

DEFAULT_SOCKET_PATH = '/run/vitals/collector.sock'


class VitalsBridgeNode(Node):
    """Reads system metrics from vitals-daemon and publishes to ROS."""

    def __init__(self):
        super().__init__('vitals_collector')

        # Parameters
        self.declare_parameter('publish_rate', 1.0)
        self.declare_parameter('topic', '/vitals/status')
        self.declare_parameter('enable_kill_service', True)
        self.declare_parameter('socket_path', DEFAULT_SOCKET_PATH)

        self._publish_rate = self.get_parameter('publish_rate').value
        self._topic = self.get_parameter('topic').value
        self._enable_kill = self.get_parameter('enable_kill_service').value
        self._socket_path = self.get_parameter('socket_path').value

        # Publisher
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            depth=1,
        )
        self._publisher = self.create_publisher(SystemStatus, self._topic, qos)

        # Kill service
        if self._enable_kill:
            # Sanitize hostname: ROS 2 names only allow alphanumerics and '_'
            hostname = socket.gethostname().replace('-', '_')
            kill_service_name = f'/{hostname}/vitals/kill_process'
            self._kill_service = self.create_service(
                KillProcess, kill_service_name, self._handle_kill_request
            )
            self.get_logger().info(f"Kill service available at {kill_service_name}")

        # Socket connection
        self._sock: socket.socket | None = None
        self._sock_lock = threading.Lock()
        self._latest_snapshot: dict | None = None
        self._snapshot_lock = threading.Lock()
        self._connected = False
        self._reconnect_interval = 2.0  # seconds between reconnect attempts
        self._last_reconnect_attempt = 0.0

        # Reader thread — continuously reads from daemon socket
        self._reader_running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        # Publish timer
        timer_period = 1.0 / self._publish_rate
        self._timer = self.create_timer(timer_period, self._publish_status)

        self.get_logger().info(
            f"Vitals bridge started, publishing to {self._topic} at {self._publish_rate} Hz"
        )
        self.get_logger().info(f"Connecting to daemon at {self._socket_path}")

    # ------------------------------------------------------------------
    # Socket communication
    # ------------------------------------------------------------------

    def _connect_to_daemon(self) -> bool:
        """Try to connect to the daemon socket. Returns True on success."""
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self._socket_path)
            with self._sock_lock:
                if self._sock is not None:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                self._sock = sock
                self._connected = True
            self.get_logger().info("Connected to vitals-daemon")
            return True
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            self._connected = False
            return False

    def _reader_loop(self):
        """Background thread: read snapshots from daemon continuously."""
        import time
        while self._reader_running:
            # Ensure connected
            if not self._connected:
                now = time.monotonic()
                if now - self._last_reconnect_attempt < self._reconnect_interval:
                    time.sleep(0.1)
                    continue
                self._last_reconnect_attempt = now
                if not self._connect_to_daemon():
                    continue

            # Read one message
            with self._sock_lock:
                sock = self._sock
            if sock is None:
                self._connected = False
                continue

            msg = self._recv_message(sock)
            if msg is None:
                # Connection lost
                self.get_logger().warn("Lost connection to vitals-daemon, reconnecting...")
                with self._sock_lock:
                    if self._sock is sock:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
                        self._connected = False
                continue

            if msg.get('type') == 'status':
                with self._snapshot_lock:
                    self._latest_snapshot = msg.get('data')
            elif msg.get('type') == 'kill_response':
                # Store for the kill service handler to pick up
                self._last_kill_response = msg

    def _recv_message(self, sock: socket.socket) -> dict | None:
        """Receive a length-prefixed JSON message. Blocking."""
        try:
            sock.settimeout(5.0)
            header = b''
            while len(header) < HEADER_SIZE:
                chunk = sock.recv(HEADER_SIZE - len(header))
                if not chunk:
                    return None
                header += chunk
            length = struct.unpack(HEADER_FMT, header)[0]
            if length > 10 * 1024 * 1024:
                return None
            payload = b''
            while len(payload) < length:
                chunk = sock.recv(length - len(payload))
                if not chunk:
                    return None
                payload += chunk
            return json.loads(payload)
        except (socket.timeout, BlockingIOError):
            return {}  # Timeout is not an error — just no data yet
        except (ConnectionResetError, OSError, json.JSONDecodeError):
            return None

    def _send_message(self, data: dict) -> bool:
        """Send a length-prefixed JSON message to daemon."""
        with self._sock_lock:
            if self._sock is None:
                return False
            try:
                payload = json.dumps(data, separators=(',', ':')).encode('utf-8')
                header = struct.pack(HEADER_FMT, len(payload))
                self._sock.sendall(header + payload)
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def _publish_status(self):
        """Convert latest daemon snapshot to ROS message and publish."""
        with self._snapshot_lock:
            snapshot = self._latest_snapshot

        if snapshot is None:
            if not self._connected:
                self.get_logger().warn(
                    "No connection to vitals-daemon", throttle_duration_sec=10.0
                )
            return

        msg = self._snapshot_to_msg(snapshot)
        self._publisher.publish(msg)

    def _snapshot_to_msg(self, snapshot: dict) -> SystemStatus:
        """Convert daemon snapshot dict to SystemStatus ROS message."""
        msg = SystemStatus()
        msg.stamp = self.get_clock().now().to_msg()

        # System metrics
        system = snapshot.get('system', {})
        msg.hostname = system.get('hostname', '')
        msg.ip_addresses = system.get('ip_addresses', [])
        msg.cpu_percent = float(system.get('cpu_percent', 0.0))
        msg.cpu_count = int(system.get('cpu_count', 0))
        msg.cpu_per_core = [float(x) for x in system.get('cpu_per_core', [])]
        msg.load_avg_1min = float(system.get('load_avg_1min', 0.0))
        msg.load_avg_5min = float(system.get('load_avg_5min', 0.0))
        msg.load_avg_15min = float(system.get('load_avg_15min', 0.0))
        msg.ram_total_bytes = int(system.get('ram_total_bytes', 0))
        msg.ram_used_bytes = int(system.get('ram_used_bytes', 0))
        msg.ram_available_bytes = int(system.get('ram_available_bytes', 0))
        msg.swap_total_bytes = int(system.get('swap_total_bytes', 0))
        msg.swap_used_bytes = int(system.get('swap_used_bytes', 0))
        msg.cpu_temperature_celsius = float(system.get('cpu_temperature_celsius', -1.0))
        msg.uptime_seconds = float(system.get('uptime_seconds', 0.0))

        # GPU metrics
        for gpu in snapshot.get('gpus', []):
            gpu_msg = GpuStatus()
            gpu_msg.index = int(gpu.get('index', 0))
            gpu_msg.name = str(gpu.get('name', ''))
            gpu_msg.utilization_percent = float(gpu.get('utilization_percent', 0.0))
            gpu_msg.memory_total_bytes = int(gpu.get('memory_total_bytes', 0))
            gpu_msg.memory_used_bytes = int(gpu.get('memory_used_bytes', 0))
            gpu_msg.temperature_celsius = float(gpu.get('temperature_celsius', -1.0))
            gpu_msg.power_watts = int(gpu.get('power_watts', -1))
            gpu_msg.fan_speed_percent = int(gpu.get('fan_speed_percent', -1))
            msg.gpus.append(gpu_msg)

        # Network interfaces
        for iface in snapshot.get('network_interfaces', []):
            net_msg = NetworkInterface()
            net_msg.name = str(iface.get('name', ''))
            net_msg.is_up = bool(iface.get('is_up', False))
            net_msg.bytes_sent_total = int(iface.get('bytes_sent_total', 0))
            net_msg.bytes_recv_total = int(iface.get('bytes_recv_total', 0))
            net_msg.packets_sent_total = int(iface.get('packets_sent_total', 0))
            net_msg.packets_recv_total = int(iface.get('packets_recv_total', 0))
            net_msg.errors_in = int(iface.get('errors_in', 0))
            net_msg.errors_out = int(iface.get('errors_out', 0))
            net_msg.drops_in = int(iface.get('drops_in', 0))
            net_msg.drops_out = int(iface.get('drops_out', 0))
            net_msg.bytes_sent_per_sec = float(iface.get('bytes_sent_per_sec', 0.0))
            net_msg.bytes_recv_per_sec = float(iface.get('bytes_recv_per_sec', 0.0))
            msg.network_interfaces.append(net_msg)

        # Disk metrics
        for disk in snapshot.get('disks', []):
            disk_msg = DiskStatus()
            disk_msg.device = str(disk.get('device', ''))
            disk_msg.mount_point = str(disk.get('mount_point', ''))
            disk_msg.filesystem = str(disk.get('filesystem', ''))
            disk_msg.total_bytes = int(disk.get('total_bytes', 0))
            disk_msg.used_bytes = int(disk.get('used_bytes', 0))
            disk_msg.free_bytes = int(disk.get('free_bytes', 0))
            disk_msg.usage_percent = float(disk.get('usage_percent', 0.0))
            disk_msg.read_bytes_per_sec = float(disk.get('read_bytes_per_sec', 0.0))
            disk_msg.write_bytes_per_sec = float(disk.get('write_bytes_per_sec', 0.0))
            msg.disks.append(disk_msg)

        # Process metrics
        for proc in snapshot.get('processes', []):
            proc_msg = self._create_process_msg(proc)
            msg.processes.append(proc_msg)

        return msg

    def _create_process_msg(self, proc: dict) -> ProcessStatus:
        """Convert process dict to ProcessStatus message."""
        proc_msg = ProcessStatus()
        proc_msg.node_name = str(proc.get('node_name', ''))
        proc_msg.node_namespace = str(proc.get('node_namespace', ''))
        proc_msg.pid = int(proc.get('pid', 0))
        proc_msg.child_pids = [int(p) for p in proc.get('child_pids', [])]
        proc_msg.cmdline = str(proc.get('cmdline', ''))
        proc_msg.container_name = str(proc.get('container_name', ''))
        proc_msg.is_launch_process = bool(proc.get('is_launch_process', False))
        proc_msg.launch_name = str(proc.get('launch_name', ''))
        proc_msg.cpu_percent = float(proc.get('cpu_percent', 0.0))
        proc_msg.ram_bytes = int(proc.get('ram_bytes', 0))
        proc_msg.ram_bytes_self = int(proc.get('ram_bytes_self', 0))
        proc_msg.gpu_index = int(proc.get('gpu_index', -1))
        proc_msg.gpu_memory_bytes = int(proc.get('gpu_memory_bytes', 0))
        proc_msg.disk_read_bytes_total = int(proc.get('disk_read_bytes_total', 0))
        proc_msg.disk_write_bytes_total = int(proc.get('disk_write_bytes_total', 0))
        proc_msg.disk_read_bytes_per_sec = float(proc.get('disk_read_bytes_per_sec', 0.0))
        proc_msg.disk_write_bytes_per_sec = float(proc.get('disk_write_bytes_per_sec', 0.0))
        proc_msg.net_rx_bytes_per_sec = float(proc.get('net_rx_bytes_per_sec', 0.0))
        proc_msg.net_tx_bytes_per_sec = float(proc.get('net_tx_bytes_per_sec', 0.0))
        proc_msg.open_files_count = int(proc.get('open_files_count', 0))
        proc_msg.network_connections_count = int(proc.get('network_connections_count', 0))
        proc_msg.num_threads = int(proc.get('num_threads', 0))
        proc_msg.status = str(proc.get('status', ''))
        proc_msg.create_time = float(proc.get('create_time', 0.0))

        for child in proc.get('child_nodes', []):
            child_msg = ChildProcessStatus()
            child_msg.node_name = str(child.get('node_name', ''))
            child_msg.node_namespace = str(child.get('node_namespace', ''))
            child_msg.pid = int(child.get('pid', 0))
            child_msg.cmdline = str(child.get('cmdline', ''))
            child_msg.cpu_percent = float(child.get('cpu_percent', 0.0))
            child_msg.ram_bytes = int(child.get('ram_bytes', 0))
            child_msg.gpu_index = int(child.get('gpu_index', -1))
            child_msg.gpu_memory_bytes = int(child.get('gpu_memory_bytes', 0))
            child_msg.disk_read_bytes_per_sec = float(child.get('disk_read_bytes_per_sec', 0.0))
            child_msg.disk_write_bytes_per_sec = float(child.get('disk_write_bytes_per_sec', 0.0))
            child_msg.net_rx_bytes_per_sec = float(child.get('net_rx_bytes_per_sec', 0.0))
            child_msg.net_tx_bytes_per_sec = float(child.get('net_tx_bytes_per_sec', 0.0))
            child_msg.status = str(child.get('status', ''))
            proc_msg.child_nodes.append(child_msg)

        return proc_msg

    # ------------------------------------------------------------------
    # Kill service
    # ------------------------------------------------------------------

    def _handle_kill_request(self, request, response):
        """Forward kill request to daemon and return response."""
        self._last_kill_response = None

        if not self._send_message({
            'type': 'kill',
            'pid': request.pid,
            'force': request.force,
        }):
            response.success = False
            response.message = "Not connected to vitals-daemon"
            return response

        # Wait for daemon response (up to 3 seconds)
        import time
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self._last_kill_response is not None:
                resp = self._last_kill_response
                response.success = resp.get('success', False)
                response.message = resp.get('message', '')
                return response
            time.sleep(0.05)

        response.success = False
        response.message = "Timeout waiting for daemon response"
        return response

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def destroy_node(self):
        """Clean up resources."""
        self._reader_running = False
        with self._sock_lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = VitalsBridgeNode()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
