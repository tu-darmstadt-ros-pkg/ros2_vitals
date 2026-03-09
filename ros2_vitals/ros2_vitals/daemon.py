"""Standalone vitals daemon — collects system metrics and serves via Unix socket.

Runs as root (for eBPF and full /proc visibility). No ROS 2 dependencies.

Usage:
    sudo python3 -m ros2_vitals.daemon [--socket-path PATH] [--rate HZ]
"""

import argparse
import json
import logging
import os
import signal
import socket
import struct
import sys
import threading
import time

# When running as root (sudo), user site-packages are not on sys.path.
# Add them if SUDO_USER is set, so optional deps like pynvml are found.
_sudo_user = os.environ.get('SUDO_USER')
if _sudo_user and os.geteuid() == 0:
    import site
    user_home = os.path.expanduser(f'~{_sudo_user}')
    user_site = site.getusersitepackages().replace(
        os.path.expanduser('~'), user_home
    )
    if user_site not in sys.path:
        sys.path.insert(0, user_site)

import psutil

from .collectors import (
    SystemCollector,
    GpuCollector,
    NetworkCollector,
    DiskCollector,
    ProcessCollector,
    NetStatsCollector,
)

logger = logging.getLogger('vitals-daemon')

HEADER_FMT = '!I'  # 4-byte big-endian unsigned int
HEADER_SIZE = struct.calcsize(HEADER_FMT)

DEFAULT_SOCKET_PATH = '/run/vitals/collector.sock'
DEFAULT_RATE = 1.0


def _send_message(sock: socket.socket, data: dict) -> bool:
    """Send a length-prefixed JSON message. Returns False on failure."""
    try:
        payload = json.dumps(data, separators=(',', ':')).encode('utf-8')
        header = struct.pack(HEADER_FMT, len(payload))
        sock.sendall(header + payload)
        return True
    except (BrokenPipeError, ConnectionResetError, OSError):
        return False


def _recv_message(sock: socket.socket, timeout: float = 0.0) -> dict | None:
    """Receive a length-prefixed JSON message. Non-blocking by default."""
    try:
        sock.settimeout(timeout)
        header = b''
        while len(header) < HEADER_SIZE:
            chunk = sock.recv(HEADER_SIZE - len(header))
            if not chunk:
                return None
            header += chunk
        length = struct.unpack(HEADER_FMT, header)[0]
        if length > 10 * 1024 * 1024:  # 10 MB sanity limit
            return None
        payload = b''
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            if not chunk:
                return None
            payload += chunk
        return json.loads(payload)
    except (socket.timeout, BlockingIOError):
        return None
    except (ConnectionResetError, OSError, json.JSONDecodeError):
        return None


class VitalsDaemon:
    """Collects system metrics and serves them to a single client via Unix socket."""

    def __init__(self, socket_path: str, rate: float):
        self._socket_path = socket_path
        self._interval = 1.0 / rate
        self._running = True

        # Client connection
        self._client: socket.socket | None = None
        self._client_lock = threading.Lock()

        # Initialize collectors
        self._system = SystemCollector()
        self._gpu = GpuCollector()
        self._network = NetworkCollector()
        self._disk = DiskCollector()
        self._net_stats = NetStatsCollector()
        self._process = ProcessCollector(self._gpu, self._net_stats)

        # Timing
        self._cycle_count = 0
        self._log_interval = 10

    def run(self):
        """Main loop: accept connections and collect/send data."""
        # Create socket directory
        sock_dir = os.path.dirname(self._socket_path)
        if sock_dir:
            os.makedirs(sock_dir, exist_ok=True)

        # Clean up stale socket file
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)

        # Create listening socket
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self._socket_path)
        # Allow non-root users to connect
        os.chmod(self._socket_path, 0o777)
        server.listen(1)
        server.settimeout(0.1)

        logger.info(f"Listening on {self._socket_path}")
        logger.info(f"Collection rate: {1.0 / self._interval} Hz")

        if self._gpu.available:
            logger.info("GPU monitoring enabled")
        else:
            logger.warning(
                "GPU monitoring unavailable (install nvidia-ml-py system-wide: "
                "sudo pip install nvidia-ml-py --break-system-packages)"
            )
        if self._net_stats.available:
            logger.info(f"Network stats backend: {self._net_stats.backend_name}")

        # Accept thread
        accept_thread = threading.Thread(target=self._accept_loop, args=(server,),
                                         daemon=True)
        accept_thread.start()

        try:
            while self._running:
                t_start = time.monotonic()

                # Collect
                snapshot = self._collect_snapshot()

                # Send to client
                with self._client_lock:
                    if self._client is not None:
                        msg = {'type': 'status', 'data': snapshot}
                        if not _send_message(self._client, msg):
                            logger.info("Client disconnected")
                            try:
                                self._client.close()
                            except OSError:
                                pass
                            self._client = None

                # Handle incoming kill requests
                self._handle_client_messages()

                # Sleep remainder of interval
                elapsed = time.monotonic() - t_start
                sleep_time = self._interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        finally:
            server.close()
            if os.path.exists(self._socket_path):
                os.unlink(self._socket_path)
            self._shutdown()

    def _accept_loop(self, server: socket.socket):
        """Accept incoming connections (runs in background thread)."""
        while self._running:
            try:
                client, _ = server.accept()
                with self._client_lock:
                    # Close previous client if any
                    if self._client is not None:
                        try:
                            self._client.close()
                        except OSError:
                            pass
                    self._client = client
                    logger.info("Client connected")
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.debug("Accept error", exc_info=True)
                break

    def _collect_snapshot(self) -> dict:
        """Collect all metrics and return as a dict."""
        timings = {}

        t0 = time.perf_counter()
        system = self._system.collect_all()
        timings['system'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        gpus = self._gpu.get_gpus() if self._gpu.available else []
        timings['gpu'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        interfaces = self._network.get_interfaces()
        timings['network'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        disks = self._disk.get_partitions()
        timings['disk'] = time.perf_counter() - t0

        t0 = time.perf_counter()
        processes = self._process.get_processes(include_children=True)
        timings['process'] = time.perf_counter() - t0

        timings['total'] = sum(timings.values())

        # Log timing periodically
        self._cycle_count += 1
        if self._cycle_count % self._log_interval == 0:
            formatted = {k: f"{v*1000:.1f}ms" for k, v in timings.items()}
            logger.info(f"Collector timings: {formatted}")

        return {
            'system': system,
            'gpus': gpus,
            'network_interfaces': interfaces,
            'disks': disks,
            'processes': processes,
        }

    def _handle_client_messages(self):
        """Check for and handle incoming messages from the client."""
        with self._client_lock:
            if self._client is None:
                return
            msg = _recv_message(self._client, timeout=0.0)

        if msg is None:
            return

        if msg.get('type') == 'kill':
            self._handle_kill(msg.get('pid', 0), msg.get('force', False))

    def _handle_kill(self, pid: int, force: bool):
        """Kill a process and send response to client."""
        response = {'type': 'kill_response', 'success': False, 'message': ''}
        try:
            proc = psutil.Process(pid)
            if force:
                proc.kill()
                response['message'] = f"Sent SIGKILL to PID {pid}"
            else:
                proc.terminate()
                response['message'] = f"Sent SIGTERM to PID {pid}"
            response['success'] = True
            logger.info(response['message'])
        except psutil.NoSuchProcess:
            response['message'] = f"Process {pid} not found"
            logger.warning(response['message'])
        except psutil.AccessDenied:
            response['message'] = f"Permission denied for PID {pid}"
            logger.warning(response['message'])
        except Exception as e:
            response['message'] = f"Failed to kill PID {pid}: {e}"
            logger.error(response['message'])

        with self._client_lock:
            if self._client is not None:
                _send_message(self._client, response)

    def _shutdown(self):
        """Clean up collectors."""
        if self._gpu.available:
            self._gpu.shutdown()
        if self._net_stats.available:
            self._net_stats.shutdown()

    def stop(self):
        """Signal the daemon to stop."""
        self._running = False


def main():
    parser = argparse.ArgumentParser(description='ROS 2 Vitals Daemon')
    parser.add_argument('--socket-path', default=DEFAULT_SOCKET_PATH,
                        help=f'Unix socket path (default: {DEFAULT_SOCKET_PATH})')
    parser.add_argument('--rate', type=float, default=DEFAULT_RATE,
                        help=f'Collection rate in Hz (default: {DEFAULT_RATE})')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='[%(levelname)s] [%(name)s]: %(message)s',
    )

    daemon = VitalsDaemon(args.socket_path, args.rate)

    def signal_handler(signum, frame):
        logger.info("Shutting down...")
        daemon.stop()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    daemon.run()


if __name__ == '__main__':
    main()
