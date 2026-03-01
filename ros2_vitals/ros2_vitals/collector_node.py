"""
ROS 2 Vitals Collector Node.

Collects system metrics and publishes them periodically.
"""

import socket
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy

import psutil

from ros2_vitals_msgs.msg import (
    SystemStatus,
    GpuStatus,
    NetworkInterface,
    DiskStatus,
    ProcessStatus,
    ChildProcessStatus,
)
from ros2_vitals_msgs.srv import KillProcess

from .collectors import (
    SystemCollector,
    GpuCollector,
    NetworkCollector,
    DiskCollector,
    ProcessCollector,
    TcpStatsCollector,
)


class VitalsCollectorNode(Node):
    """
    Lightweight system monitor node that publishes machine stats.

    Collects CPU, RAM, GPU, disk, network, and ROS process statistics.
    """

    def __init__(self):
        super().__init__('vitals_collector')

        # Declare parameters
        self.declare_parameter('publish_rate', 1.0)
        self.declare_parameter('topic', '/vitals/status')
        self.declare_parameter('monitor_gpu', True)
        self.declare_parameter('monitor_processes', True)
        self.declare_parameter('monitor_network', True)
        self.declare_parameter('monitor_disk', True)
        self.declare_parameter('include_children', True)
        self.declare_parameter('enable_kill_service', True)

        # Get parameters
        self._publish_rate = self.get_parameter('publish_rate').value
        self._topic = self.get_parameter('topic').value
        self._monitor_gpu = self.get_parameter('monitor_gpu').value
        self._monitor_processes = self.get_parameter('monitor_processes').value
        self._monitor_network = self.get_parameter('monitor_network').value
        self._monitor_disk = self.get_parameter('monitor_disk').value
        self._include_children = self.get_parameter('include_children').value
        self._enable_kill_service = self.get_parameter('enable_kill_service').value

        # Initialize collectors
        self._system_collector = SystemCollector()
        self._gpu_collector = GpuCollector() if self._monitor_gpu else None
        self._network_collector = NetworkCollector() if self._monitor_network else None
        self._disk_collector = DiskCollector() if self._monitor_disk else None
        self._tcp_stats_collector = TcpStatsCollector() if self._monitor_processes else None
        self._process_collector = (
            ProcessCollector(self._gpu_collector, self._tcp_stats_collector)
            if self._monitor_processes else None
        )

        # Create publisher with reliable QoS
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            depth=1,
        )
        self._publisher = self.create_publisher(SystemStatus, self._topic, qos)

        # Create kill service namespaced by hostname
        # Service will be at /<hostname>/vitals/kill_process
        if self._enable_kill_service:
            hostname = socket.gethostname()
            kill_service_name = f'/{hostname}/vitals/kill_process'
            self._kill_service = self.create_service(
                KillProcess, kill_service_name, self._handle_kill_request
            )
            self.get_logger().info(f"Kill service available at {kill_service_name}")

        # Timing instrumentation
        self._timing_cycle_count = 0
        self._timing_log_interval = 10  # Log every 10 cycles

        # Create timer for periodic publishing
        timer_period = 1.0 / self._publish_rate
        self._timer = self.create_timer(timer_period, self._publish_status)

        self.get_logger().info(
            f"Vitals collector started, publishing to {self._topic} at {self._publish_rate} Hz"
        )
        if self._gpu_collector and self._gpu_collector.available:
            self.get_logger().info("GPU monitoring enabled")

    def _publish_status(self):
        """Collect and publish system status."""
        timings = {}
        msg = SystemStatus()
        msg.stamp = self.get_clock().now().to_msg()

        # System metrics
        t0 = time.perf_counter()
        system = self._system_collector.collect_all()
        timings['system'] = time.perf_counter() - t0
        msg.hostname = system['hostname']
        msg.ip_addresses = system['ip_addresses']
        msg.cpu_percent = system['cpu_percent']
        msg.cpu_count = system['cpu_count']
        msg.cpu_per_core = system['cpu_per_core']
        msg.load_avg_1min = system['load_avg_1min']
        msg.load_avg_5min = system['load_avg_5min']
        msg.load_avg_15min = system['load_avg_15min']
        msg.ram_total_bytes = system['ram_total_bytes']
        msg.ram_used_bytes = system['ram_used_bytes']
        msg.ram_available_bytes = system['ram_available_bytes']
        msg.swap_total_bytes = system['swap_total_bytes']
        msg.swap_used_bytes = system['swap_used_bytes']
        msg.cpu_temperature_celsius = system['cpu_temperature_celsius']
        msg.uptime_seconds = system['uptime_seconds']

        # GPU metrics
        t0 = time.perf_counter()
        if self._gpu_collector:
            for gpu in self._gpu_collector.get_gpus():
                gpu_msg = GpuStatus()
                gpu_msg.index = gpu['index']
                gpu_msg.name = gpu['name']
                gpu_msg.utilization_percent = gpu['utilization_percent']
                gpu_msg.memory_total_bytes = gpu['memory_total_bytes']
                gpu_msg.memory_used_bytes = gpu['memory_used_bytes']
                gpu_msg.temperature_celsius = gpu['temperature_celsius']
                gpu_msg.power_watts = gpu['power_watts']
                gpu_msg.fan_speed_percent = gpu['fan_speed_percent']
                msg.gpus.append(gpu_msg)
        timings['gpu'] = time.perf_counter() - t0

        # Network metrics
        t0 = time.perf_counter()
        if self._network_collector:
            for iface in self._network_collector.get_interfaces():
                net_msg = NetworkInterface()
                net_msg.name = iface['name']
                net_msg.is_up = iface['is_up']
                net_msg.bytes_sent_total = iface['bytes_sent_total']
                net_msg.bytes_recv_total = iface['bytes_recv_total']
                net_msg.packets_sent_total = iface['packets_sent_total']
                net_msg.packets_recv_total = iface['packets_recv_total']
                net_msg.errors_in = iface['errors_in']
                net_msg.errors_out = iface['errors_out']
                net_msg.drops_in = iface['drops_in']
                net_msg.drops_out = iface['drops_out']
                net_msg.bytes_sent_per_sec = iface['bytes_sent_per_sec']
                net_msg.bytes_recv_per_sec = iface['bytes_recv_per_sec']
                msg.network_interfaces.append(net_msg)
        timings['network'] = time.perf_counter() - t0

        # Disk metrics
        t0 = time.perf_counter()
        if self._disk_collector:
            for disk in self._disk_collector.get_partitions():
                disk_msg = DiskStatus()
                disk_msg.device = disk['device']
                disk_msg.mount_point = disk['mount_point']
                disk_msg.filesystem = disk['filesystem']
                disk_msg.total_bytes = disk['total_bytes']
                disk_msg.used_bytes = disk['used_bytes']
                disk_msg.free_bytes = disk['free_bytes']
                disk_msg.usage_percent = disk['usage_percent']
                disk_msg.read_bytes_per_sec = disk['read_bytes_per_sec']
                disk_msg.write_bytes_per_sec = disk['write_bytes_per_sec']
                msg.disks.append(disk_msg)
        timings['disk'] = time.perf_counter() - t0

        # Process metrics
        t0 = time.perf_counter()
        if self._process_collector:
            for proc in self._process_collector.get_processes(self._include_children):
                proc_msg = self._create_process_msg(proc)
                msg.processes.append(proc_msg)
        timings['process'] = time.perf_counter() - t0

        # Publish
        t0 = time.perf_counter()
        self._publisher.publish(msg)
        timings['publish'] = time.perf_counter() - t0

        timings['total'] = sum(timings.values())

        # Log timing periodically
        self._timing_cycle_count += 1
        if self._timing_cycle_count % self._timing_log_interval == 0:
            formatted = {k: f"{v*1000:.1f}ms" for k, v in timings.items()}
            self.get_logger().info(f"Collector timings: {formatted}")

            # Log sub-timings for the expensive collectors
            if hasattr(self._system_collector, '_sub_timings'):
                sub = {k: f"{v*1000:.1f}ms" for k, v in self._system_collector._sub_timings.items()}
                self.get_logger().info(f"  system breakdown: {sub}")
            if self._process_collector and hasattr(self._process_collector, '_sub_timings'):
                sub = self._process_collector._sub_timings
                formatted_sub = {}
                for k, v in sub.items():
                    if isinstance(v, float):
                        formatted_sub[k] = f"{v*1000:.1f}ms"
                    else:
                        formatted_sub[k] = str(v)
                self.get_logger().info(f"  process breakdown: {formatted_sub}")

    def _create_process_msg(self, proc: dict) -> ProcessStatus:
        """Create a ProcessStatus message from process data dict."""
        proc_msg = ProcessStatus()
        proc_msg.node_name = proc['node_name']
        proc_msg.node_namespace = proc['node_namespace']
        proc_msg.pid = proc['pid']
        proc_msg.child_pids = proc['child_pids']
        proc_msg.cmdline = proc['cmdline']
        proc_msg.container_name = proc['container_name']
        proc_msg.is_launch_process = proc.get('is_launch_process', False)
        proc_msg.launch_name = proc.get('launch_name', '')
        proc_msg.cpu_percent = proc['cpu_percent']
        proc_msg.ram_bytes = proc['ram_bytes']
        proc_msg.ram_bytes_self = proc['ram_bytes_self']
        proc_msg.gpu_index = proc['gpu_index']
        proc_msg.gpu_memory_bytes = proc['gpu_memory_bytes']
        proc_msg.disk_read_bytes_total = proc['disk_read_bytes_total']
        proc_msg.disk_write_bytes_total = proc['disk_write_bytes_total']
        proc_msg.disk_read_bytes_per_sec = proc['disk_read_bytes_per_sec']
        proc_msg.disk_write_bytes_per_sec = proc['disk_write_bytes_per_sec']
        proc_msg.net_rx_bytes_per_sec = proc.get('net_rx_bytes_per_sec', 0.0)
        proc_msg.net_tx_bytes_per_sec = proc.get('net_tx_bytes_per_sec', 0.0)
        proc_msg.open_files_count = proc['open_files_count']
        proc_msg.network_connections_count = proc['network_connections_count']
        proc_msg.num_threads = proc['num_threads']
        proc_msg.status = proc['status']
        proc_msg.create_time = proc['create_time']

        # Add child nodes
        for child in proc.get('child_nodes', []):
            child_msg = ChildProcessStatus()
            child_msg.node_name = child['node_name']
            child_msg.node_namespace = child['node_namespace']
            child_msg.pid = child['pid']
            child_msg.cmdline = child['cmdline']
            child_msg.cpu_percent = child['cpu_percent']
            child_msg.ram_bytes = child['ram_bytes']
            child_msg.gpu_index = child['gpu_index']
            child_msg.gpu_memory_bytes = child['gpu_memory_bytes']
            child_msg.disk_read_bytes_per_sec = child['disk_read_bytes_per_sec']
            child_msg.disk_write_bytes_per_sec = child['disk_write_bytes_per_sec']
            child_msg.net_rx_bytes_per_sec = child.get('net_rx_bytes_per_sec', 0.0)
            child_msg.net_tx_bytes_per_sec = child.get('net_tx_bytes_per_sec', 0.0)
            child_msg.status = child['status']
            proc_msg.child_nodes.append(child_msg)

        return proc_msg

    def _handle_kill_request(self, request, response):
        """Handle kill process service request."""
        pid = request.pid
        force = request.force

        try:
            proc = psutil.Process(pid)

            if force:
                proc.kill()  # SIGKILL
                response.message = f"Sent SIGKILL to PID {pid}"
            else:
                proc.terminate()  # SIGTERM
                response.message = f"Sent SIGTERM to PID {pid}"

            response.success = True
            self.get_logger().info(response.message)

        except psutil.NoSuchProcess:
            response.success = False
            response.message = f"Process {pid} not found"
            self.get_logger().warn(response.message)

        except psutil.AccessDenied:
            response.success = False
            response.message = f"Permission denied for PID {pid}"
            self.get_logger().warn(response.message)

        except Exception as e:
            response.success = False
            response.message = f"Failed to kill PID {pid}: {str(e)}"
            self.get_logger().error(response.message)

        return response

    def destroy_node(self):
        """Clean up resources."""
        if self._gpu_collector:
            self._gpu_collector.shutdown()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = VitalsCollectorNode()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
